"""Route-level tests for GET /personal-channels/openclaw/catalog
(server_modules/routes_personal_channels.py).

WHY THE ROUTE EXISTS
---------------------
The unified channel grid (FleetAgentDetail.tsx's ChannelsTab) renders the
transported (OpenClaw) cards from `agentGatewayId ? [...] : []` — with no
gateway bound to an agent, the whole transported half of the grid vanished,
silently. The first-party cards (WhatsApp, Signal, iMessage) show a "Needs
Gateway" pill in that exact situation instead of disappearing; the
transported ones had no equivalent because there was no way for the browser
to learn the catalog without a `gateway_id` in the URL.

`openclaw_channel_setup_catalog()` was always gateway-independent (a pure
read of the checked-in manifest — see its own docstring). This route is the
missing entry point to it, and these tests pin: (1) it needs no gateway_id
at all, (2) it returns the same channel shape the gateway-scoped `/setup`
route's `channels` key returns, (3) it carries no `observed`/`gateway_id`
key (those are properties of a live box, and this route never touches one),
and (4) plain login is enough — there is nothing tenant-scoped to leak.
"""

from __future__ import annotations

import importlib
import unittest
from typing import Any, Dict

from fastapi import FastAPI
from fastapi.testclient import TestClient


class RoutesOpenClawCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.routes = importlib.import_module("server_modules.routes_personal_channels")
        self.setup_service = importlib.import_module("server_modules.openclaw_channel_setup_service")

        self.app = FastAPI()
        self.app.include_router(self.routes.router, prefix="/api")
        self.app.dependency_overrides[self.routes.require_api_key] = self._current_user_override
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.app.dependency_overrides.clear()

    @staticmethod
    def _current_user_override() -> Dict[str, Any]:
        return {
            "auth_type": "bearer",
            "is_admin": False,
            "user_id": "user-a",
            "id": "user-a",
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

    # -- tests -----------------------------------------------------------

    def test_returns_the_catalog_with_no_gateway_id_anywhere(self) -> None:
        response = self.client.get("/api/personal-channels/openclaw/catalog")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertNotIn("gateway_id", body)
        self.assertNotIn("observed", body)
        self.assertNotIn("observed_error", body)
        self.assertIn("channels", body)
        self.assertIn("openclaw_version", body)
        self.assertIn("already_available_channels", body)

    def test_channels_match_the_live_setup_catalog_verbatim(self) -> None:
        """This route must never carry its own copy of the channel list — it
        is the same `openclaw_channel_setup_catalog()` the gateway-scoped
        `/setup` route joins against observed device state."""
        response = self.client.get("/api/personal-channels/openclaw/catalog")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["channels"], self.setup_service.openclaw_channel_setup_catalog())
        self.assertGreater(len(body["channels"]), 0, "the catalog must not be empty")

    def test_every_channel_carries_the_fields_the_frontend_generated_form_needs(self) -> None:
        response = self.client.get("/api/personal-channels/openclaw/catalog")
        body = response.json()
        for channel in body["channels"]:
            for key in (
                "channel_key",
                "channel_id",
                "label",
                "connect_method",
                "selection_label",
                "docs_path",
                "fields",
                "requires_plugin",
                "plugin_id",
            ):
                self.assertIn(key, channel, f"{channel.get('channel_id')!r} is missing {key!r}")

    def test_a_plain_logged_in_member_can_read_it(self) -> None:
        """No gateway registration to check, no workspace scoping to enforce
        — a static product catalog needs nothing past "someone is logged
        in"."""
        response = self.client.get("/api/personal-channels/openclaw/catalog")
        self.assertEqual(response.status_code, 200)

    def test_the_route_path_is_asserted_as_a_personal_channel_route(self) -> None:
        """`assert_personal_route_path` is called on every route in this
        file; a route that skipped it would be invisible to the lane-contract
        drift checks that assume every /personal-channels/* path goes
        through it."""
        import inspect

        source = inspect.getsource(self.routes.get_openclaw_channel_catalog)
        self.assertIn("assert_personal_route_path", source)


if __name__ == "__main__":
    unittest.main()
