"""Route-level tests for POST /personal-channels/openclaw/gateways/{id}/provision
(server_modules/routes_personal_channels.py).

WHY THE ROUTE EXISTS, which is what these tests are really pinning down:
before it, `openclaw_provisioning_service.provision_openclaw_gateway` had
exactly one caller — `reconcile_openclaw_policy_best_effort`, reached only from
PATCH .../group-policy — and the gateway-side boot reconcile
(OpenClawProvisioningRuntime.reconcileFromLastAppliedPolicy) is a documented
no-op on a box that has never been provisioned. So the FIRST provisioning run
on any machine had no entry point, and every trigger downstream of it was dead
in practice. Verified live 2026-08-08 by provisioning a real OpenClaw instance
through this route.

The two behaviours below are the ones that distinguish a setup action from the
settings-save path it used to hide behind:

  * failure is LOUD. The group-policy path is best-effort on purpose (a
    settings save that persisted must not 500 because a laptop is asleep) and
    returns `openclaw_provisioning: null`. For an explicit "set this up" that
    is indistinguishable from success, so this route raises.
  * a REFUSAL from the box is a 200 carrying the whole report. A refusal is a
    successful round trip that answers "no, and here is why" — wrong OpenClaw
    version, failed lockdown read-back, unclean security audit, channels that
    could not carry the owner's setting. Collapsing that into an HTTP error
    throws away everything except one sentence.

Auth/ownership enforcement is shared with the group-policy routes
(_require_accessible_gateway_registration) and covered in
test_routes_personal_channels_group_policy.py; the cross-tenant case is
re-asserted here because this route installs a supervised process on someone
else's computer, which is not a check to inherit silently.
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


class RoutesOpenClawProvisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gateway_state_repository = importlib.import_module("server_modules.gateway_state_repository")
        self.routes = importlib.import_module("server_modules.routes_personal_channels")
        self.provisioning = importlib.import_module("server_modules.openclaw_provisioning_service")

        self.tmpdir = tempfile.TemporaryDirectory()
        self.gateway_db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        self.gateway_state_repository.init_gateway_state_db(self.gateway_db_path)
        self.db_patcher = patch.object(
            self.gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.gateway_db_path
        )
        self.db_patcher.start()

        self.app = FastAPI()
        self.app.include_router(self.routes.router, prefix="/api")
        self.app.dependency_overrides[self.routes.require_api_key] = self._current_user_override
        self.client = TestClient(self.app)
        self._active_user: Dict[str, Any] = self._owner_a_user()

        pairing = self.gateway_state_repository.create_pairing_intent(
            tenant_id="tenant-a",
            workspace_id="ws-a",
            user_id="user-a",
            ttl_seconds=600,
            display_name="Owner A's Mac",
            platform="macos",
        )
        registration = self.gateway_state_repository.register_gateway_from_pairing(
            pairing_token=pairing["pairing_token"],
            device_id="device-a-1",
            display_name="Owner A's Mac",
            platform="macos-arm64",
            capabilities=["openclaw.provision"],
        )
        self.gateway_id = registration["gateway_id"]

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()
        self.db_patcher.stop()
        self.tmpdir.cleanup()

    def _current_user_override(self):
        return self._active_user

    @staticmethod
    def _owner_a_user() -> Dict[str, Any]:
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-a",
            "id": "user-a",
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
    def _other_tenant_user() -> Dict[str, Any]:
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-b",
            "id": "user-b",
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

    def _url(self, *, agent_id: str = "agent-owner-a") -> str:
        return f"/api/personal-channels/openclaw/gateways/{self.gateway_id}/provision?agent_id={agent_id}"

    # -- tests ---------------------------------------------------------------

    def test_provisions_and_returns_the_boxs_verbatim_report(self) -> None:
        report = {
            "gateway_id": self.gateway_id,
            "run_id": "openclaw-provision-abc123",
            "status": "provisioned",
            "profile": "empyralis",
            "openclaw_version": "2026.6.10",
            "config_changed": True,
            "restart_required": True,
            "disabled_channels": [],
            "widenings": [],
            "lockdown_violations": [],
        }
        with patch.object(
            self.provisioning, "provision_openclaw_gateway", AsyncMock(return_value=report)
        ) as provision:
            response = self.client.post(self._url())

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["gateway_id"], self.gateway_id)
        self.assertEqual(body["agent_id"], "agent-owner-a")
        self.assertEqual(body["openclaw_provisioning"], report)
        # The registration is the source of tenant/workspace, never the caller.
        kwargs = provision.await_args.kwargs
        self.assertEqual(kwargs["tenant_id"], "tenant-a")
        self.assertEqual(kwargs["workspace_id"], "ws-a")
        self.assertEqual(kwargs["agent_id"], "agent-owner-a")

    def test_a_refusal_from_the_box_is_200_with_the_reason_intact(self) -> None:
        """`refused` means the box answered. The owner needs the code and the
        per-channel findings, so this must not become an HTTPException that can
        only carry one sentence."""
        report = {
            "gateway_id": self.gateway_id,
            "run_id": "openclaw-provision-def456",
            "status": "refused",
            "profile": "empyralis",
            "refusal": {
                "code": "openclaw_security_audit_not_clean",
                "detail": "`openclaw security audit` is not clean: gateway.bind_public [critical] Gateway is reachable off-box",
            },
            "disabled_channels": [
                {
                    "channel_id": "zalo",
                    "code": "require_mention_off_not_expressible",
                    "detail": "This channel is turned off because OpenClaw cannot carry the setting you chose.",
                }
            ],
        }
        with patch.object(
            self.provisioning, "provision_openclaw_gateway", AsyncMock(return_value=report)
        ):
            response = self.client.post(self._url())

        self.assertEqual(response.status_code, 200)
        provisioning = response.json()["openclaw_provisioning"]
        self.assertEqual(provisioning["status"], "refused")
        self.assertEqual(provisioning["refusal"]["code"], "openclaw_security_audit_not_clean")
        self.assertEqual(provisioning["disabled_channels"][0]["channel_id"], "zalo")

    def test_an_unreachable_box_fails_loudly_rather_than_silently_succeeding(self) -> None:
        """The whole reason this route exists instead of reusing
        reconcile_openclaw_policy_best_effort: that one swallows this and
        returns None, which for a setup action is indistinguishable from
        "it worked"."""
        with patch.object(
            self.provisioning,
            "provision_openclaw_gateway",
            AsyncMock(side_effect=self.provisioning.OpenClawProvisioningError(
                "Gateway is not currently connected.", status_code=409
            )),
        ):
            response = self.client.post(self._url())

        self.assertEqual(response.status_code, 409)
        self.assertIn("not currently connected", response.json()["detail"])

    def test_missing_agent_id_is_refused_rather_than_provisioning_the_fallback_policy(self) -> None:
        """An unresolved agent identity resolves to the fail-closed fallback
        policy; writing THAT into OpenClaw's config would look like a choice
        the owner made."""
        with patch.object(
            self.provisioning, "provision_openclaw_gateway", AsyncMock()
        ) as provision:
            response = self.client.post(
                f"/api/personal-channels/openclaw/gateways/{self.gateway_id}/provision"
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("agent_id", response.json()["detail"])
        provision.assert_not_awaited()

    def test_a_user_of_another_tenant_cannot_provision_this_computer(self) -> None:
        self._active_user = self._other_tenant_user()
        with patch.object(
            self.provisioning, "provision_openclaw_gateway", AsyncMock()
        ) as provision:
            response = self.client.post(self._url())

        self.assertIn(response.status_code, (403, 404))
        provision.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
