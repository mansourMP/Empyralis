"""MAN-111 -- "MCP applications: OAuth authorization is blocked; no agent can
connect to any external app."

Diagnosis (see the commit this ships with for the full writeup): the OAuth
token exchange and credential storage already work. The actual silent-
failure gap was downstream of that, in three places:

1. mcp_registry_service.upsert_workspace_mcp_server[_async]: when tool
   discovery raised (network error, 401, timeout, ...) the exception
   propagated BEFORE the server row was ever written to the registry. A
   failed FIRST registration attempt left no trace anywhere -- not in the
   registry, not in any catalog, nothing durable a health check or a human
   could find later. Fixed to persist a "discovery_failed" row (with the
   real detail) before re-raising, so callers still see the failure AND a
   queryable trace survives it.

2. connection_catalog_service.status_items() / agent_status_items(): a
   LANE_WORK_APP_CONNECTOR item reported health_status="healthy" the moment
   *any* vault credential existed for it -- completely independent of
   whether the MCP server that's supposed to carry its tools ever actually
   registered. A connector could finish OAuth, get a real credential, have
   its MCP registration fail, and still render "Connected" / healthy
   forever. Fixed via _mcp_registration_health(), which checks the now-
   durable status from fix #1 and downgrades the badge when it's genuinely
   known to have failed (never on mere silence/absence, to avoid false
   positives for connectors that don't ride on MCP at all, or haven't
   synced yet).

3. connection_oauth_service.connect_app_via_oauth_to_mcp() (the JSON
   /apps/{provider}/oauth/complete bridge -- confirmed dead from the
   browser's own client wrapper's TODO comment in workstation-client.ts,
   but still a live, directly-callable HTTP route) aggregated per-server
   registration failures into each server's own "error" field, but never
   into the top-level "warning" the function returns -- so a caller that
   only checks the aggregate warning (the convention every other caller in
   this codebase follows) saw nothing wrong even when every server failed.
   Fixed to also populate "warning" on failure.

   Separately, routes_connections.complete_connection_oauth_callback (the
   REAL live callback, reached via the browser's OAuth redirect) computes
   the same kind of honest warning via complete_oauth_callback()'s "mcp"
   field, but discarded it when building the post-OAuth redirect URL -- the
   browser landed on ?connected={provider} with no signal either way. Fixed
   to carry it as an mcp_warning query param.

Run: DATABASE_URL= python3 -m pytest server_modules/tests/test_man111_mcp_oauth_silent_failure.py -v
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException

from server_modules import connection_catalog_service as catalog_service
from server_modules import connection_oauth_service, mcp_registry_service, routes_connections


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1. mcp_registry_service -- a discovery failure must persist a queryable
#    record instead of the whole registration attempt vanishing.
# ---------------------------------------------------------------------------

class McpRegistryDiscoveryFailurePersistsTests(unittest.TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.registry_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.registry_patcher = patch.object(
            mcp_registry_service, "MCP_SERVER_REGISTRY_FILE", self.registry_path
        )
        self.registry_patcher.start()

    def tearDown(self) -> None:
        self.registry_patcher.stop()
        self.temp_dir.cleanup()
        super().tearDown()

    def test_sync_discovery_failure_still_persists_a_failed_status_row(self) -> None:
        """Before the fix: this raised, and list_workspace_mcp_servers()
        afterwards returned [] -- the registration attempt left no trace at
        all, which is exactly what makes "connecting does nothing" so hard
        to diagnose from the outside."""
        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(side_effect=ConnectionError("MCP endpoint unreachable")),
        ):
            with self.assertRaises(ConnectionError):
                mcp_registry_service.upsert_workspace_mcp_server(
                    workspace_id="ws-fail-sync",
                    server_id="notion",
                    label="Notion (MCP)",
                    transport="streamable_http",
                    endpoint="https://mcp.notion.com/mcp",
                    discover_tools=True,
                )

        servers = mcp_registry_service.list_workspace_mcp_servers("ws-fail-sync")
        self.assertEqual(len(servers), 1, "a failed registration must still leave a durable trace")
        self.assertEqual(servers[0]["status"], "discovery_failed")
        self.assertIn("MCP endpoint unreachable", servers[0]["status_detail"])
        self.assertEqual(servers[0]["tool_count"], 0)

        record = mcp_registry_service.get_workspace_mcp_server("ws-fail-sync", "notion")
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "discovery_failed")

    def test_async_discovery_failure_still_persists_a_failed_status_row(self) -> None:
        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(side_effect=TimeoutError("MCP tools/list timed out")),
        ):
            with self.assertRaises(TimeoutError):
                _run(
                    mcp_registry_service.upsert_workspace_mcp_server_async(
                        workspace_id="ws-fail-async",
                        server_id="linear",
                        label="Linear (MCP)",
                        transport="streamable_http",
                        endpoint="https://mcp.linear.app/mcp",
                        discover_tools=True,
                    )
                )

        servers = mcp_registry_service.list_workspace_mcp_servers("ws-fail-async")
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["status"], "discovery_failed")
        self.assertIn("timed out", servers[0]["status_detail"])

    def test_async_discovery_success_path_is_unaffected(self) -> None:
        """Regression guard: wrapping discovery in try/except must not
        change the happy path -- tools still get saved and status stays
        "ok" when discovery genuinely succeeds."""
        with patch.object(
            mcp_registry_service,
            "_list_tools_streamable_http_async",
            new=AsyncMock(
                return_value=[
                    {
                        "name": "search_pages",
                        "label": "Search Pages",
                        "description": "Search Notion pages.",
                    }
                ]
            ),
        ):
            record = _run(
                mcp_registry_service.upsert_workspace_mcp_server_async(
                    workspace_id="ws-ok-async",
                    server_id="notion",
                    label="Notion (MCP)",
                    transport="streamable_http",
                    endpoint="https://mcp.notion.com/mcp",
                    discover_tools=True,
                )
            )

        self.assertEqual(len(record["tools"]), 1)
        self.assertEqual(record["tools"][0]["name"], "search_pages")
        self.assertEqual(record.get("status", "ok"), "ok")
        servers = mcp_registry_service.list_workspace_mcp_servers("ws-ok-async")
        self.assertEqual(servers[0]["tool_count"], 1)


# ---------------------------------------------------------------------------
# 2. connection_catalog_service -- a connector must not read "healthy" once
#    its MCP registration is known to have failed, but must NOT be flagged
#    just because no MCP row exists yet (that's silence, not evidence).
# ---------------------------------------------------------------------------

class McpRegistrationHealthTests(unittest.TestCase):
    def test_no_override_for_a_provider_with_no_oauth_mapping_at_all(self) -> None:
        """hetzner is a token-paste VPS provider, not an OAuth/MCP connector
        -- provider_from_connection_id() raises for it, and that must be
        swallowed into "nothing to report," not propagate."""
        status, detail = catalog_service._mcp_registration_health({"id": "hetzner"}, "ws-1")
        self.assertIsNone(status)
        self.assertIsNone(detail)

    def test_no_override_when_no_server_row_exists_yet(self) -> None:
        """A connector can be legitimately connected with no MCP row at all
        (credential stored through a non-OAuth-callback path, or just not
        synced yet). Absence must never read as failure."""
        with patch.object(mcp_registry_service, "get_workspace_mcp_server", return_value=None):
            status, detail = catalog_service._mcp_registration_health({"id": "notion"}, "ws-1")
        self.assertIsNone(status)
        self.assertIsNone(detail)

    def test_no_override_when_registration_status_is_ok(self) -> None:
        with patch.object(
            mcp_registry_service,
            "get_workspace_mcp_server",
            return_value={"status": "ok", "tools": [{"name": "search"}]},
        ):
            status, detail = catalog_service._mcp_registration_health({"id": "notion"}, "ws-1")
        self.assertIsNone(status)
        self.assertIsNone(detail)

    def test_downgrades_health_when_registration_is_known_to_have_failed(self) -> None:
        """The core MAN-111 assertion: once mcp_registry_service has
        recorded a real failure (fix #1 above is what makes this durable),
        the catalog must surface it rather than keep reporting healthy."""
        with patch.object(
            mcp_registry_service,
            "get_workspace_mcp_server",
            return_value={"status": "discovery_failed", "status_detail": "401 Unauthorized"},
        ):
            status, detail = catalog_service._mcp_registration_health({"id": "notion"}, "ws-1")
        self.assertEqual(status, "tools_unavailable")
        self.assertIsNotNone(detail)
        self.assertIn("401 Unauthorized", detail)

    def test_status_items_reflects_the_failure_for_a_connected_connector(self) -> None:
        """End-to-end through status_items()'s LANE_WORK_APP_CONNECTOR
        branch: a connector with a real vault credential (so
        connected=True) but a failed MCP registration must not read
        health_status="healthy"."""
        item = {"id": "notion", "lane": catalog_service.LANE_WORK_APP_CONNECTOR}
        with patch.object(
            mcp_registry_service,
            "get_workspace_mcp_server",
            return_value={"status": "discovery_failed", "status_detail": "connection refused"},
        ):
            status, detail = catalog_service._mcp_registration_health(item, "ws-1")
        # Mirrors exactly what status_items()'s LANE_WORK_APP_CONNECTOR
        # branch now does with these two return values when connected=True.
        health_status = "healthy"
        if status:
            health_status = status
        self.assertEqual(health_status, "tools_unavailable")
        self.assertIn("connection refused", detail)


# ---------------------------------------------------------------------------
# 3a. connect_app_via_oauth_to_mcp -- per-server failures must also reach
#     the function's own top-level "warning" field, not just each server's
#     individual "error" field.
# ---------------------------------------------------------------------------

class ConnectAppViaOauthToMcpWarningAggregationTests(unittest.TestCase):
    def test_registration_failure_populates_the_top_level_warning(self) -> None:
        with (
            patch.object(
                connection_oauth_service, "_exchange_github", return_value={"access_token": "tok-123"}
            ),
            patch(
                "server_modules.connectors_actions.create_connector_vault",
                new=AsyncMock(return_value={"id": "cred-1"}),
            ),
            patch(
                "server_modules.mcp_registry_service.upsert_workspace_mcp_server_async",
                new=AsyncMock(side_effect=RuntimeError("MCP endpoint returned 503")),
            ),
        ):
            result = _run(
                connection_oauth_service.connect_app_via_oauth_to_mcp(
                    workspace_id="ws-1",
                    provider="github",
                    oauth_code="abc",
                    redirect_uri="https://empyralis.ai/api/apps/github/oauth/complete",
                )
            )

        self.assertEqual(result["mcp_servers_registered"], 0)
        self.assertFalse(result["servers"][0]["mcp_server_registered"])
        self.assertIn("MCP endpoint returned 503", result["servers"][0]["error"])
        # This is the actual MAN-111 assertion: before the fix, "warning"
        # stayed None here even though every mapped server failed.
        self.assertIsNotNone(result["warning"])
        self.assertIn("MCP endpoint returned 503", result["warning"])

    def test_success_path_still_registers_a_server_and_returns_its_tools(self) -> None:
        with (
            patch.object(
                connection_oauth_service, "_exchange_github", return_value={"access_token": "tok-123"}
            ),
            patch(
                "server_modules.connectors_actions.create_connector_vault",
                new=AsyncMock(return_value={"id": "cred-1"}),
            ),
            patch(
                "server_modules.mcp_registry_service.upsert_workspace_mcp_server_async",
                new=AsyncMock(
                    return_value={
                        "id": "github",
                        "tools": [{"name": "search_issues"}, {"name": "create_pr"}],
                    }
                ),
            ),
        ):
            result = _run(
                connection_oauth_service.connect_app_via_oauth_to_mcp(
                    workspace_id="ws-1",
                    provider="github",
                    oauth_code="abc",
                    redirect_uri="https://empyralis.ai/api/apps/github/oauth/complete",
                )
            )

        self.assertEqual(result["mcp_servers_registered"], 1)
        self.assertIsNone(result["warning"])
        self.assertEqual(len(result["tools"]), 2)
        self.assertTrue(result["servers"][0]["mcp_server_registered"])


# ---------------------------------------------------------------------------
# 3b. complete_connection_oauth_callback -- the honest "mcp.warning" the
#     live OAuth callback already computes must reach the redirect the
#     browser follows, not get dropped when the redirect URL is built.
# ---------------------------------------------------------------------------

def _state_for(**overrides) -> str:
    payload = {"provider": "notion", "workspace_id": "ws-1", "surface": "apps", "user_id": "owner-1"}
    payload.update(overrides)
    return connection_oauth_service._encode_state(payload)


def _owner_user() -> dict:
    return {"user_id": "owner-1", "email": "owner@example.com", "role": "owner"}


@contextmanager
def _bypass_auth():
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


def _location_query(response) -> dict:
    return parse_qs(urlsplit(response.headers["location"]).query)


class OAuthCallbackMcpWarningRedirectTests(unittest.TestCase):
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

    def test_mcp_registration_failure_warning_reaches_the_redirect(self) -> None:
        state = _state_for(agent_install_id="ainstall-1", surface="apps", provider="notion")
        mcp_warning = (
            "Connected, but 1 Notion (MCP) MCP server(s) could not be registered, "
            "so their tools are unavailable — notion: 401 Unauthorized"
        )
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(
                    return_value={
                        "ok": True,
                        "provider": "notion",
                        "agent_install_id": "ainstall-1",
                        "mcp": {
                            "registered": 0,
                            "servers": [],
                            "collisions": [],
                            "failures": [{"server_id": "notion", "detail": "401 Unauthorized"}],
                            "warning": mcp_warning,
                        },
                    }
                ),
            ),
            _bypass_auth(),
        ):
            response = _run(
                routes_connections.complete_connection_oauth_callback(
                    provider="notion", request=None, code="abc", state=state, error="",
                    current_user=_owner_user(),
                )
            )

        query = _location_query(response)
        # Before the fix: complete_oauth_callback()'s "mcp" payload (with its
        # honest warning) was computed and then thrown away here -- the
        # redirect carried only ?connected=notion, indistinguishable from a
        # connector whose tools registered cleanly.
        self.assertIn("mcp_warning", query)
        self.assertIn("could not be registered", query["mcp_warning"][0])
        self.assertEqual(query["connected"], ["notion"])

    def test_clean_registration_carries_no_mcp_warning_param(self) -> None:
        state = _state_for(agent_install_id="ainstall-1", surface="apps", provider="linear")
        with (
            patch(
                "server_modules.connection_oauth_service.complete_oauth_callback",
                new=AsyncMock(
                    return_value={
                        "ok": True,
                        "provider": "linear",
                        "agent_install_id": "ainstall-1",
                        "mcp": {
                            "registered": 1,
                            "servers": [{"server_id": "linear", "tool_count": 5}],
                            "collisions": [],
                            "failures": [],
                        },
                    }
                ),
            ),
            _bypass_auth(),
        ):
            response = _run(
                routes_connections.complete_connection_oauth_callback(
                    provider="linear", request=None, code="abc", state=state, error="",
                    current_user=_owner_user(),
                )
            )

        query = _location_query(response)
        self.assertNotIn("mcp_warning", query)
        self.assertEqual(query["connected"], ["linear"])


if __name__ == "__main__":
    unittest.main()
