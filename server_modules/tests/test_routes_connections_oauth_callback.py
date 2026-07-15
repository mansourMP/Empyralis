"""routes_connections.py -- OAuth callback dead-end + wrong-tab fixes.

Two related bugs in complete_connection_oauth_callback's completion-redirect
building (_oauth_completion_url):

1. [dead-end] The `except HTTPException` branch built the completion
   redirect WITHOUT agent_install_id/project_id, so a failure during an
   agent-scoped OAuth connect (started from an agent's Connectors or
   Channels tab) stranded the user on the generic /integrations page
   instead of round-tripping back to the agent's own tab with
   connection_error intact. The most common trigger -- the user clicking
   "Cancel" on the provider's consent screen -- raises before the try
   block's own state decode even runs, so the fix includes a best-effort
   re-decode in the except branch for that case too.

2. [wrong-tab] A successful per-agent Slack/Discord (channel) OAuth connect
   redirected to the agent's CONNECTORS tab even when the flow began on the
   agent's Channels tab -- _oauth_completion_url's agent-context branch
   ignored `surface` entirely, even though the no-agent-context branch a
   few lines below it already used `surface` to pick "channels" vs "apps".
   FleetAgentDetail.tsx's ChannelsTab always sends surface="sage";
   ConnectorPicker.tsx (rendered only inside ConnectorsTab) always sends
   surface="apps" -- an existing, reliable signal that just wasn't being
   read in the agent-context branch.
"""

from __future__ import annotations

import asyncio
import unittest
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException

from server_modules import connection_oauth_service, routes_connections


def _run(coro):
    return asyncio.run(coro)


def _owner_user() -> dict:
    return {"user_id": "owner-1", "email": "owner@example.com", "role": "owner"}


@contextmanager
def _bypass_auth():
    """Bypass real workspace-access enforcement -- covered by its own
    dedicated tests (test_routes_fleet_auth_guard.py); these tests are about
    the completion-redirect logic, not the auth gate."""
    with (
        patch(
            "server_modules.auth.enforce_workspace_access",
            lambda current_user, workspace_id, minimum_role="viewer": workspace_id,
        ),
        patch(
            "server_modules.auth.workspace_tenant_id",
            lambda current_user, workspace_id: "tenant-1",
        ),
    ):
        yield


def _state_for(**overrides) -> str:
    payload = {"provider": "slack", "workspace_id": "ws-1", "surface": "sage", "user_id": "owner-1"}
    payload.update(overrides)
    return connection_oauth_service._encode_state(payload)


def _path_and_query(url: str) -> tuple[str, dict]:
    split = urlsplit(url)
    return split.path, parse_qs(split.query)


def _location_path_and_query(response) -> tuple[str, dict]:
    return _path_and_query(response.headers["location"])


class OAuthCompletionUrlTabRoutingTests(unittest.TestCase):
    """_oauth_completion_url -- the agent-context branch must respect
    `surface` the same way the no-agent-context branch below it already
    does."""

    def setUp(self):
        patcher = patch(
            "server_modules.connection_oauth_service.request_origin",
            return_value="https://app.example.com",
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_agent_context_channel_surface_goes_to_channels_tab(self):
        url = routes_connections._oauth_completion_url(
            None, workspace_id="ws-1", provider="slack", surface="sage",
            agent_install_id="ainstall-1", project_id="proj-1",
        )
        path, query = _path_and_query(url)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/channels")
        self.assertEqual(query["connected"], ["slack"])

    def test_agent_context_connector_surface_goes_to_connectors_tab(self):
        url = routes_connections._oauth_completion_url(
            None, workspace_id="ws-1", provider="notion", surface="apps",
            agent_install_id="ainstall-1", project_id="proj-1",
        )
        path, _query = _path_and_query(url)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/connectors")

    def test_agent_context_missing_surface_defaults_to_connectors_tab(self):
        """Safe fallback: preserves the pre-fix behavior (always /connectors)
        for any caller that doesn't send a recognized surface."""
        url = routes_connections._oauth_completion_url(
            None, workspace_id="ws-1", provider="notion", surface=None,
            agent_install_id="ainstall-1", project_id="proj-1",
        )
        path, _query = _path_and_query(url)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/connectors")

    def test_agent_context_error_still_respects_surface_and_carries_error(self):
        url = routes_connections._oauth_completion_url(
            None, workspace_id="ws-1", provider="slack", surface="sage",
            agent_install_id="ainstall-1", project_id="proj-1", error="token_exchange_failed",
        )
        path, query = _path_and_query(url)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/channels")
        self.assertEqual(query["connection_error"], ["token_exchange_failed"])
        self.assertNotIn("connected", query)

    def test_no_agent_context_falls_back_to_generic_integrations_page(self):
        url = routes_connections._oauth_completion_url(
            None, workspace_id="ws-1", provider="notion", surface="apps",
        )
        path, query = _path_and_query(url)
        self.assertEqual(path, "/w/ws-1/integrations")
        self.assertEqual(query["section"], ["apps"])


class OAuthCallbackErrorRoundTripTests(unittest.TestCase):
    """complete_connection_oauth_callback -- the except branch must round-
    trip an agent-scoped OAuth failure back to that agent's own tab, not
    strand the user on the generic /integrations page."""

    def setUp(self):
        patcher = patch(
            "server_modules.connection_oauth_service.request_origin",
            return_value="https://app.example.com",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        bundle_patcher = patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={"project_id": "proj-1"}),
        )
        bundle_patcher.start()
        self.addCleanup(bundle_patcher.stop)
        tenant_patcher = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value="tenant-1"),
        )
        tenant_patcher.start()
        self.addCleanup(tenant_patcher.stop)

    def test_failure_after_state_decode_round_trips_to_agent_channels_tab(self):
        """The common case: state decodes fine (agent_install_id present),
        but the token exchange itself fails later (complete_oauth_callback
        raises). Previously this landed on the generic /integrations page,
        with no way back to the agent."""
        state = _state_for(agent_install_id="ainstall-1", surface="sage")
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Slack token exchange failed.")),
            ),
            _bypass_auth(),
        ):
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="slack", request=None, code="abc", state=state, error="",
                current_user=_owner_user(),
            ))
        self.assertEqual(response.status_code, 303)
        path, query = _location_path_and_query(response)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/channels")
        self.assertEqual(query["connection_error"], ["Slack token exchange failed."])

    def test_provider_declined_before_decode_still_recovers_agent_context(self):
        """The single most common failure: the user clicks "Cancel" on the
        provider's consent screen. The provider echoes back `error=` AND the
        original `state` -- this must still recover agent_install_id (and
        the real workspace_id) via the except branch's best-effort
        re-decode, even though the main try block's own decode never ran
        (short-circuited by `if error: raise`, which fires first)."""
        state = _state_for(agent_install_id="ainstall-1", surface="apps", workspace_id="ws-custom")
        with _bypass_auth():
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="slack", request=None, code="", state=state, error="access_denied",
                current_user=_owner_user(),
            ))
        self.assertEqual(response.status_code, 303)
        path, query = _location_path_and_query(response)
        # Proves the re-decode actually ran (not just a coincidental default):
        # workspace_id in the path came from the recovered state, not the
        # function's hardcoded "ws-1" fallback.
        self.assertEqual(path, "/w/ws-custom/projects/proj-1/agents/ainstall-1/connectors")
        self.assertEqual(query["connection_error"], ["access_denied"])

    def test_garbled_state_falls_back_gracefully_to_integrations_page(self):
        """No agent context is recoverable from a garbled state -- must fall
        back to the generic page, not crash."""
        with _bypass_auth():
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="slack", request=None, code="", state="not-a-real-state", error="access_denied",
                current_user=_owner_user(),
            ))
        self.assertEqual(response.status_code, 303)
        path, query = _location_path_and_query(response)
        self.assertEqual(path, "/w/ws-1/integrations")
        self.assertEqual(query["connection_error"], ["access_denied"])

    def test_no_agent_install_id_in_state_falls_back_to_integrations_page(self):
        """A plain (non-agent-scoped) OAuth connect failing must keep
        landing on the generic page -- unaffected by this fix."""
        state = _state_for(surface="apps")
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(side_effect=HTTPException(status_code=400, detail="Notion token exchange failed.")),
            ),
            _bypass_auth(),
        ):
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="notion", request=None, code="abc", state=state, error="",
                current_user=_owner_user(),
            ))
        self.assertEqual(response.status_code, 303)
        path, query = _location_path_and_query(response)
        self.assertEqual(path, "/w/ws-1/integrations")
        self.assertEqual(query["section"], ["apps"])


class OAuthCallbackSuccessTabRoutingTests(unittest.TestCase):
    """complete_connection_oauth_callback -- success path must send a
    channel connect back to Channels and a connector connect back to
    Connectors."""

    def setUp(self):
        patcher = patch(
            "server_modules.connection_oauth_service.request_origin",
            return_value="https://app.example.com",
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        bundle_patcher = patch(
            "server_modules.agent_registry_repository.get_workspace_agent_install_bundle",
            new=AsyncMock(return_value={"project_id": "proj-1"}),
        )
        bundle_patcher.start()
        self.addCleanup(bundle_patcher.stop)
        tenant_patcher = patch(
            "server_modules.control_plane_repository.resolve_tenant_id_for_workspace",
            new=AsyncMock(return_value="tenant-1"),
        )
        tenant_patcher.start()
        self.addCleanup(tenant_patcher.stop)

    def test_successful_channel_connect_returns_to_channels_tab(self):
        state = _state_for(agent_install_id="ainstall-1", surface="sage")
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(return_value={"ok": True, "provider": "slack", "agent_install_id": "ainstall-1"}),
            ),
            _bypass_auth(),
        ):
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="slack", request=None, code="abc", state=state, error="",
                current_user=_owner_user(),
            ))
        path, query = _location_path_and_query(response)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/channels")
        self.assertEqual(query["connected"], ["slack"])

    def test_successful_connector_connect_returns_to_connectors_tab(self):
        state = _state_for(agent_install_id="ainstall-1", surface="apps", provider="notion")
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(return_value={"ok": True, "provider": "notion", "agent_install_id": "ainstall-1"}),
            ),
            _bypass_auth(),
        ):
            response = _run(routes_connections.complete_connection_oauth_callback(
                provider="notion", request=None, code="abc", state=state, error="",
                current_user=_owner_user(),
            ))
        path, _query = _location_path_and_query(response)
        self.assertEqual(path, "/w/ws-1/projects/proj-1/agents/ainstall-1/connectors")


class StateHasUserIdForLogTests(unittest.TestCase):
    """_state_has_user_id_for_log -- must never raise. Pre-fix, the
    OAUTH_CALLBACK log line called connection_oauth_service.decode_state(state)
    unguarded, before the callback's own try/except was in play; decode_state
    raises HTTPException on invalid/expired state, so a garbled or missing
    state (e.g. the callback URL hit directly) 500'd the whole callback --
    no redirect at all, not even the generic /integrations dead end."""

    def test_garbled_state_returns_false_not_raise(self):
        self.assertFalse(routes_connections._state_has_user_id_for_log("not-a-real-state"))

    def test_empty_state_returns_false_not_raise(self):
        self.assertFalse(routes_connections._state_has_user_id_for_log(""))

    def test_valid_state_with_user_id_returns_true(self):
        state = _state_for(user_id="owner-1")
        self.assertTrue(routes_connections._state_has_user_id_for_log(state))


if __name__ == "__main__":
    unittest.main()
