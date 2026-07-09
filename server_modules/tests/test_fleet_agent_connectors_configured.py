"""2026-07-09 first-run integrity fix: the agent-connectors catalog must tell
the frontend which OAuth apps aren't configured on this deployment BEFORE
the click, not only after (routes_fleet.fleet_agent_connectors)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from server_modules import routes_fleet


def _run(coro):
    return asyncio.run(coro)


class FleetAgentConnectorsConfiguredFieldTests(unittest.TestCase):
    _CATALOG_ITEMS = [
        {
            "id": "github",
            "lane": "work_app_connector",
            "display_name": "GitHub",
            "description": "Connect GitHub for repository actions.",
            "connected": False,
            "setup_kind": "oauth",
            "next_action": "connect",
            "health_status": "unknown",
            "auth_required_fields": [],
        },
        {
            "id": "custom_api",
            "lane": "work_app_connector",
            "display_name": "Custom API",
            "description": "Bring your own HTTP endpoint.",
            "connected": False,
            "setup_kind": "manual",
            "next_action": "connect",
            "health_status": "unknown",
            "auth_required_fields": ["api_key"],
        },
    ]

    def test_unconfigured_oauth_provider_is_flagged(self):
        with (
            patch(
                "server_modules.connection_catalog_service.agent_status_items",
                new=lambda **kwargs: _AwaitableList(self._CATALOG_ITEMS),
            ),
            patch(
                "server_modules.connection_oauth_service.oauth_provider_configured",
                return_value=False,
            ),
        ):
            result = _run(routes_fleet.fleet_agent_connectors(
                request=None, workspace_id="ws-1", agent_id="agent-1",
            ))

        self.assertTrue(result["ok"])
        github = next(c for c in result["connectors"] if c["id"] == "github")
        self.assertFalse(github["configured"])

    def test_configured_oauth_provider_is_not_flagged(self):
        with (
            patch(
                "server_modules.connection_catalog_service.agent_status_items",
                new=lambda **kwargs: _AwaitableList(self._CATALOG_ITEMS),
            ),
            patch(
                "server_modules.connection_oauth_service.oauth_provider_configured",
                return_value=True,
            ),
        ):
            result = _run(routes_fleet.fleet_agent_connectors(
                request=None, workspace_id="ws-1", agent_id="agent-1",
            ))

        github = next(c for c in result["connectors"] if c["id"] == "github")
        self.assertTrue(github["configured"])

    def test_non_oauth_connector_is_always_configured(self):
        """A manual/API-key connector isn't gated by the OAuth env-var check
        at all — it must never show as inert for a reason that doesn't apply."""
        with (
            patch(
                "server_modules.connection_catalog_service.agent_status_items",
                new=lambda **kwargs: _AwaitableList(self._CATALOG_ITEMS),
            ),
            patch(
                "server_modules.connection_oauth_service.oauth_provider_configured",
                return_value=False,
            ),
        ):
            result = _run(routes_fleet.fleet_agent_connectors(
                request=None, workspace_id="ws-1", agent_id="agent-1",
            ))

        custom_api = next(c for c in result["connectors"] if c["id"] == "custom_api")
        self.assertTrue(custom_api["configured"])


class _AwaitableList:
    """Minimal awaitable wrapper so a plain lambda can stand in for an async
    function under patch() without needing AsyncMock's call-tracking."""

    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _coro():
            return self._value
        return _coro().__await__()


if __name__ == "__main__":
    unittest.main()
