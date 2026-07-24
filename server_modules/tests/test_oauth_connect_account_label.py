"""§1.5 (Multiplayer Projects plan) — accounts render as "default".

Verified gap: connectors_actions.store_agent_connector_credential defaults
account_label to "default" (:2819/:2853), and connection_oauth_service.
complete_oauth_callback's agent-scoped branch (:3766-3781) never set it to
anything else -- every OAuth-connected account rendered as "default" in the
UI, the one signal that would let a human notice a §1.2/§1.3 cross-account
mixup before damage.

Fix: complete_oauth_callback now calls the new
_probe_oauth_account_identity() right after token exchange -- reusing the
SAME profile_probe endpoint "Test connection" already calls
(OAuthProviderConfig.profile_probe) via the SAME runtime_common.
http_json_request helper every other provider probe in this codebase uses --
and passes the real identity (or "default" on any probe failure) as
account_label.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from server_modules import connection_oauth_service, connectors_actions


def _run(coro):
    return asyncio.run(coro)


def _state_for(**overrides) -> str:
    payload = {
        "provider": "google_workspace",
        "workspace_id": "ws-1",
        "surface": "sage",
        "user_id": "owner-1",
    }
    payload.update(overrides)
    return connection_oauth_service._encode_state(payload)


def _fake_request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={"x-forwarded-proto": "http", "x-forwarded-host": "localhost:3000"},
        base_url="http://127.0.0.1:8001/",
    )


class ProbeOauthAccountIdentityUnitTests(unittest.TestCase):
    """Direct tests of the new helper -- a pure, best-effort function."""

    def test_returns_email_field_when_profile_probe_succeeds(self) -> None:
        with patch(
            "server_modules.runtime_common.http_json_request",
            return_value={"status": 200, "json": {"email": "person@example.com"}},
        ):
            identity = connection_oauth_service._probe_oauth_account_identity(
                "google_workspace", {"access_token": "tok-abc"},
            )
        self.assertEqual(identity, "person@example.com")

    def test_falls_back_through_field_names_for_a_different_profile_shape(self) -> None:
        """Microsoft 365's userinfo shape uses "mail", not "email"."""
        with patch(
            "server_modules.runtime_common.http_json_request",
            return_value={"status": 200, "json": {"mail": "person@corp.example"}},
        ):
            identity = connection_oauth_service._probe_oauth_account_identity(
                "microsoft_365", {"access_token": "tok-abc"},
            )
        self.assertEqual(identity, "person@corp.example")

    def test_returns_empty_string_when_no_access_token(self) -> None:
        identity = connection_oauth_service._probe_oauth_account_identity("google_workspace", {})
        self.assertEqual(identity, "")

    def test_returns_empty_string_on_non_2xx_status(self) -> None:
        with patch(
            "server_modules.runtime_common.http_json_request",
            return_value={"status": 401, "json": {}},
        ):
            identity = connection_oauth_service._probe_oauth_account_identity(
                "google_workspace", {"access_token": "tok-abc"},
            )
        self.assertEqual(identity, "")

    def test_returns_empty_string_when_http_call_raises(self) -> None:
        """Never raises -- this is visibility, not a security gate."""
        with patch(
            "server_modules.runtime_common.http_json_request",
            side_effect=RuntimeError("network unreachable"),
        ):
            identity = connection_oauth_service._probe_oauth_account_identity(
                "google_workspace", {"access_token": "tok-abc"},
            )
        self.assertEqual(identity, "")

    def test_returns_empty_string_for_provider_with_no_profile_probe(self) -> None:
        identity = connection_oauth_service._probe_oauth_account_identity(
            "unknown_provider_not_in_configs", {"access_token": "tok-abc"},
        )
        self.assertEqual(identity, "")

    def test_returns_empty_string_when_profile_has_no_recognized_identity_field(self) -> None:
        with patch(
            "server_modules.runtime_common.http_json_request",
            return_value={"status": 200, "json": {"id": "12345", "team_id": "T1"}},
        ):
            identity = connection_oauth_service._probe_oauth_account_identity(
                "google_workspace", {"access_token": "tok-abc"},
            )
        self.assertEqual(identity, "")


class OAuthConnectWritesRealAccountLabelTests(unittest.TestCase):
    """Integration-level: complete_oauth_callback's agent-scoped branch must
    pass the probed identity as account_label -- not leave every connection
    rendering as "default"."""

    def setUp(self) -> None:
        super().setUp()
        self.request_origin_patcher = patch.object(
            connection_oauth_service, "request_origin", return_value="https://app.example.com",
        )
        self.request_origin_patcher.start()
        self.tenant_patcher = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value="tenant-1"),
        )
        self.tenant_patcher.start()
        self.register_mcp_patcher = patch.object(
            connection_oauth_service,
            "_register_mcp_servers_for_provider",
            new=AsyncMock(return_value={"registered": 0, "servers": [], "collisions": []}),
        )
        self.register_mcp_patcher.start()

    def tearDown(self) -> None:
        self.register_mcp_patcher.stop()
        self.tenant_patcher.stop()
        self.request_origin_patcher.stop()
        super().tearDown()

    def test_agent_scoped_connect_writes_the_probed_email_as_account_label(self) -> None:
        state = _state_for(agent_install_id="ainstall-1")
        captured = {}

        async def fake_store_agent_connector_credential(**kwargs):
            captured.update(kwargs)
            return {"id": "cred-1", "provider": kwargs["provider"], "connector_key": kwargs["provider"]}

        with (
            patch.object(
                connection_oauth_service, "_exchange_google",
                return_value={"auth_mode": "oauth", "access_token": "tok-abc"},
            ),
            patch.object(
                connectors_actions, "store_agent_connector_credential",
                new=fake_store_agent_connector_credential,
            ),
            patch(
                "server_modules.runtime_common.http_json_request",
                return_value={"status": 200, "json": {"email": "person@example.com"}},
            ),
        ):
            result = _run(connection_oauth_service.complete_oauth_callback(
                provider="google_workspace", code="provider-code", state=state, request=_fake_request(),
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(captured.get("account_label"), "person@example.com")

    def test_agent_scoped_connect_falls_back_to_default_when_probe_fails(self) -> None:
        """Never a false identity, and never a crash -- a failed probe keeps
        today's exact fallback ("default"), it just doesn't improve on it."""
        state = _state_for(agent_install_id="ainstall-1")
        captured = {}

        async def fake_store_agent_connector_credential(**kwargs):
            captured.update(kwargs)
            return {"id": "cred-1", "provider": kwargs["provider"], "connector_key": kwargs["provider"]}

        with (
            patch.object(
                connection_oauth_service, "_exchange_google",
                return_value={"auth_mode": "oauth", "access_token": "tok-abc"},
            ),
            patch.object(
                connectors_actions, "store_agent_connector_credential",
                new=fake_store_agent_connector_credential,
            ),
            patch(
                "server_modules.runtime_common.http_json_request",
                side_effect=RuntimeError("network unreachable"),
            ),
        ):
            result = _run(connection_oauth_service.complete_oauth_callback(
                provider="google_workspace", code="provider-code", state=state, request=_fake_request(),
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(captured.get("account_label"), "default")

    def test_bare_workspace_connect_without_agent_install_id_is_unaffected(self) -> None:
        """The non-agent-scoped path (create_connector_vault) has no
        account_label concept at all -- this fix must not touch it."""
        state = _state_for()  # no agent_install_id
        captured = {}

        async def fake_create_connector_vault(body):
            captured["body"] = body
            return {"id": "cred-1", "provider": body.connector, "workspace_id": body.workspace_id, "metadata": body.metadata}

        with (
            patch.object(
                connection_oauth_service, "_exchange_google",
                return_value={"auth_mode": "oauth", "access_token": "tok-abc"},
            ),
            patch.object(connectors_actions, "create_connector_vault", new=fake_create_connector_vault),
            patch(
                "server_modules.runtime_common.http_json_request",
                return_value={"status": 200, "json": {"email": "person@example.com"}},
            ) as mock_probe,
        ):
            result = _run(connection_oauth_service.complete_oauth_callback(
                provider="google_workspace", code="provider-code", state=state, request=_fake_request(),
            ))

        self.assertTrue(result["ok"])
        self.assertEqual(captured["body"].connector, "google_workspace")
        # The probe helper is only wired into the agent-scoped branch today.
        mock_probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
