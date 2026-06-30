from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request

from server_modules import routes_connectors


def _api_routes() -> list[APIRoute]:
    return [route for route in routes_connectors.router.routes if isinstance(route, APIRoute)]


def _route_dependencies(path: str) -> list[str]:
    for route in _api_routes():
        if route.path == path:
            return [getattr(dep.call, "__name__", str(dep.call)) for dep in route.dependant.dependencies]
    raise AssertionError(f"Route {path} not found.")


def _json_request(path: str, payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": ("203.0.113.12", 54123),
        },
        receive,
    )


class ConnectorRouteSecurityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        routes_connectors.PUBLIC_WEBHOOK_RATE_LIMIT_BUCKETS.clear()

    def test_only_verified_connector_webhook_routes_are_public(self) -> None:
        public_paths = {
            route.path
            for route in _api_routes()
            if route.path.startswith(("/channels", "/connectors")) and not route.dependant.dependencies
        }
        self.assertEqual(
            public_paths,
            {
                "/channels/telegram/webhook/{connector_id}",
                "/channels/whatsapp/twilio/webhook",
                "/channels/slack/events",
                "/channels/github/webhook",
                "/connectors/discord/webhook",
            },
        )

    def test_operational_autopilot_routes_remain_backend_authenticated(self) -> None:
        self.assertIn("require_api_key", _route_dependencies("/channels/telegram/autopilot/status"))
        self.assertIn("require_api_key", _route_dependencies("/channels/whatsapp/autopilot/status"))
        self.assertIn("require_api_key", _route_dependencies("/channels/discord/bot-runtime/status"))
        self.assertIn("require_api_key", _route_dependencies("/channels/autopilot/profiles"))

    def test_provider_catalog_route_enforces_workspace_access(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/providers",
                "query_string": b"workspace_id=finance",
                "headers": [],
            }
        )
        current_user = {"workspace_ids": {"finance"}, "workspace_roles": {"finance": "owner"}}
        with patch.object(routes_connectors, "enforce_workspace_access", return_value="finance") as enforce_mock, patch.object(
            routes_connectors.core,
            "list_providers",
            new=AsyncMock(return_value={"providers": []}),
        ) as list_mock:
            result = asyncio.run(routes_connectors.providers_catalog(request, current_user=current_user))

        enforce_mock.assert_called_once_with(
            current_user,
            "finance",
            minimum_role="member",
        )
        list_mock.assert_awaited_once_with(workspace_id="finance")
        self.assertEqual(result, {"providers": []})

    def test_provider_model_catalog_route_enforces_member_workspace_access(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/providers/catalog",
                "query_string": b"workspace_id=finance",
                "headers": [],
            }
        )
        current_user = {"workspace_ids": {"finance"}, "workspace_roles": {"finance": "member"}}
        with patch.object(routes_connectors, "enforce_workspace_access", return_value="finance") as enforce_mock, patch.object(
            routes_connectors.provider_catalog_service,
            "list_workspace_provider_catalog",
            new=AsyncMock(return_value={"providers": []}),
        ) as list_mock:
            result = asyncio.run(routes_connectors.providers_model_catalog(request, current_user=current_user))

        enforce_mock.assert_called_once_with(
            current_user,
            "finance",
            minimum_role="member",
        )
        list_mock.assert_awaited_once_with(workspace_id="finance")
        self.assertEqual(result, {"providers": []})

    def test_public_studio_webhook_wrapper_fails_closed_when_lane_contract_rejects_path(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/channels/slack/events",
                "query_string": b"",
                "headers": [],
            }
        )
        with patch.object(
            routes_connectors.channel_lane_contract_service,
            "assert_public_studio_webhook_path",
            side_effect=ValueError("lane rejected"),
        ) as assert_mock, patch.object(
            routes_connectors.actions,
            "slack_events_webhook",
            new=AsyncMock(return_value={"ok": True}),
        ) as slack_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(routes_connectors.slack_events_webhook(request))

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, "lane rejected")
        assert_mock.assert_called_once_with("/channels/slack/events")
        slack_mock.assert_not_awaited()

    def test_public_telegram_webhook_wrapper_uses_canonical_studio_lane_path(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/channels/telegram/webhook/tg-1",
                "query_string": b"",
                "headers": [],
                "client": ("203.0.113.10", 54123),
            }
        )
        with patch.object(
            routes_connectors.channel_lane_contract_service,
            "assert_public_studio_webhook_path",
            return_value={"runtime_lane": "studio_business_connector"},
        ) as assert_mock, patch.object(
            routes_connectors.actions,
            "telegram_webhook_canonical",
            new=AsyncMock(return_value={"ok": True, "handled": 1}),
        ) as telegram_mock:
            result = asyncio.run(routes_connectors.telegram_webhook(request, "tg-1"))

        assert_mock.assert_called_once_with("/channels/telegram/webhook")
        telegram_mock.assert_awaited_once_with(request, "tg-1")
        self.assertEqual(result, {"ok": True, "handled": 1})

    def test_public_webhook_wrapper_rate_limits_per_ip_and_path(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/channels/slack/events",
                "query_string": b"",
                "headers": [],
                "client": ("203.0.113.11", 54123),
            }
        )
        with patch.object(
            routes_connectors.channel_lane_contract_service,
            "assert_public_studio_webhook_path",
            return_value={"runtime_lane": "studio_business_connector"},
        ), patch.object(
            routes_connectors,
            "PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE",
            1,
        ):
            first = asyncio.run(
                routes_connectors._dispatch_public_studio_webhook(
                    request=request,
                    path="/channels/slack/events",
                    delegate=AsyncMock(return_value={"ok": True}),
                )
            )
            self.assertEqual(first, {"ok": True})
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(
                    routes_connectors._dispatch_public_studio_webhook(
                        request=request,
                        path="/channels/slack/events",
                        delegate=AsyncMock(return_value={"ok": True}),
                    )
                )

        self.assertEqual(exc_info.exception.status_code, 429)

    def test_slack_oauth_callback_rejects_bad_redirect_uri(self) -> None:
        request = _json_request(
            "/connectors/slack/oauth/callback",
            {
                "code": "slack-code",
                "redirect_uri": "https://evil.example.com/oauth/slack",
                "workspace_id": "finance",
            },
        )
        with patch.dict("os.environ", {"FRONTEND_ORIGINS": "https://app.empyralis.com"}, clear=False):
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(routes_connectors.actions.slack_oauth_callback(request, current_user={"user_id": "owner-1"}))

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertIn("not allowed", str(exc_info.exception.detail).lower())

    def test_slack_oauth_callback_enforces_workspace_access_before_persisting(self) -> None:
        request = _json_request(
            "/connectors/slack/oauth/callback",
            {
                "code": "slack-code",
                "redirect_uri": "https://app.empyralis.com/oauth/slack",
                "workspace_id": "finance",
            },
        )
        current_user = {"user_id": "owner-1", "workspace_access": {"other": {"role": "owner"}}}
        with patch.dict("os.environ", {"FRONTEND_ORIGINS": "https://app.empyralis.com"}, clear=False), patch.object(
            routes_connectors.actions,
            "slack_exchange_oauth_code",
            return_value={"credentials": {"access_token": "xoxb-test"}},
        ) as exchange_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(routes_connectors.actions.slack_oauth_callback(request, current_user=current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        exchange_mock.assert_not_called()

    def test_slack_oauth_callback_persists_after_valid_redirect_and_workspace(self) -> None:
        request = _json_request(
            "/connectors/slack/oauth/callback",
            {
                "code": "slack-code",
                "redirect_uri": "https://app.empyralis.com/oauth/slack",
                "workspace_id": "finance",
                "label": "Team Slack",
            },
        )
        with patch.dict("os.environ", {"FRONTEND_ORIGINS": "https://app.empyralis.com"}, clear=False), patch(
            "server_modules.auth.enforce_workspace_access",
            return_value="finance",
        ) as access_mock, patch.object(
            routes_connectors.actions,
            "slack_exchange_oauth_code",
            return_value={"credentials": {"access_token": "xoxb-test"}},
        ) as exchange_mock, patch.object(
            routes_connectors.actions,
            "validate_slack_connector",
            return_value={"ok": True, "credentials": {"access_token": "xoxb-test"}, "team": {"id": "T1"}},
        ) as validate_mock, patch.object(
            routes_connectors.actions,
            "_upsert_slack_oauth_connector_entry",
            return_value={"id": "cred-slack-1", "connector": "slack", "workspace_id": "finance"},
        ) as upsert_mock:
            result = asyncio.run(
                routes_connectors.actions.slack_oauth_callback(
                    request,
                    current_user={"user_id": "owner-1"},
                )
            )

        self.assertTrue(result["ok"])
        access_mock.assert_called_once()
        exchange_mock.assert_called_once()
        validate_mock.assert_called_once()
        upsert_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
