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
                "/channels/sms/twilio/webhook",
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

    # ── Cross-tenant authorization sweep, 2026-08-13 ──────────────────────
    #
    # telegram_send_message/telegram_autopilot_test_message/probe_provider/
    # get_provider_models/enable_provider_profile/disable_provider_profile/
    # delete_provider_profile/update_tool_contract/rotate_vault_key were all
    # registered with either a bare `require_api_key` (authentication only,
    # any tenant) or `require_admin_api_key` (a ROLE check -- any owner of
    # ANY tenant -- not a tenancy check), with NO comparison between the
    # caller's own workspace/tenant and the resource actually being read or
    # mutated. CONFIRMED live against a seeded two-tenant stack: an
    # authenticated owner of workspace B could name workspace A's
    # workspace_id and have Empyralis decrypt/use workspace A's Telegram bot
    # token, AI-provider credential, or provider failover profile -- and any
    # owner-role caller (not even an operator) could globally disable a tool
    # or rotate the whole platform's vault passphrase.

    def test_telegram_send_route_enforces_workspace_access_before_using_connector(self) -> None:
        from server_modules.runtime_models import TelegramSendRequest

        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        body = TelegramSendRequest(text="hi", workspace_id="ws-a", chat_id="attacker-chat")
        with patch.object(
            routes_connectors,
            "enforce_workspace_access",
            side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
        ) as enforce_mock, patch.object(
            routes_connectors.actions,
            "telegram_send_message",
            new=AsyncMock(),
        ) as send_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(routes_connectors.telegram_send_message_route(body, current_user=current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="member")
        send_mock.assert_not_awaited()

    def test_telegram_autopilot_test_message_route_enforces_workspace_access_before_using_connector(self) -> None:
        from server_modules.runtime_models import TelegramAutopilotTestRequest

        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        body = TelegramAutopilotTestRequest(text="hi", workspace_id="ws-a", chat_id="attacker-chat")
        with patch.object(
            routes_connectors,
            "enforce_workspace_access",
            side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
        ) as enforce_mock, patch.object(
            routes_connectors.actions,
            "telegram_autopilot_test_message",
            new=AsyncMock(),
        ) as send_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(routes_connectors.telegram_autopilot_test_message_route(body, current_user=current_user))

        self.assertEqual(exc_info.exception.status_code, 403)
        enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="member")
        send_mock.assert_not_awaited()

    def test_probe_provider_route_enforces_workspace_access_for_bare_workspace_id(self) -> None:
        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        with patch.object(
            routes_connectors,
            "enforce_workspace_access",
            side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
        ) as enforce_mock, patch.object(
            routes_connectors.core,
            "probe_provider",
            new=AsyncMock(),
        ) as probe_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(
                    routes_connectors.probe_provider_route(
                        "openai",
                        credential_id="cred-a",
                        workspace_id="ws-a",
                        current_user=current_user,
                    )
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="owner")
        probe_mock.assert_not_awaited()

    def test_probe_provider_route_authorizes_against_profiles_owning_workspace_not_the_callers_supplied_one(self) -> None:
        # The vulnerable shape: a caller supplies THEIR OWN valid workspace_id
        # alongside another tenant's profile_id. The fix must authorize
        # against the profile's real owning workspace, never the raw
        # caller-supplied one, or a valid-looking workspace_id would launder
        # access to a profile/credential that belongs to someone else.
        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        with patch.object(
            routes_connectors.core,
            "get_provider_profile_workspace_id",
            return_value="ws-a",
        ) as profile_lookup_mock, patch.object(
            routes_connectors,
            "enforce_workspace_access",
            side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
        ) as enforce_mock, patch.object(
            routes_connectors.core,
            "probe_provider",
            new=AsyncMock(),
        ) as probe_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(
                    routes_connectors.probe_provider_route(
                        "openai",
                        workspace_id="ws-b",
                        profile_id="profile-owned-by-a",
                        current_user=current_user,
                    )
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        profile_lookup_mock.assert_called_once_with("profile-owned-by-a")
        # Authorized against the PROFILE's workspace (ws-a), not the raw
        # caller-supplied one (ws-b) -- that's the whole fix.
        enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="owner")
        probe_mock.assert_not_awaited()

    def test_get_provider_models_route_authorizes_against_profiles_owning_workspace(self) -> None:
        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        with patch.object(
            routes_connectors.core,
            "get_provider_profile_workspace_id",
            return_value="ws-a",
        ), patch.object(
            routes_connectors,
            "enforce_workspace_access",
            side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
        ) as enforce_mock, patch.object(
            routes_connectors.core,
            "get_provider_models",
            new=AsyncMock(),
        ) as models_mock:
            with self.assertRaises(HTTPException):
                asyncio.run(
                    routes_connectors.get_provider_models_route(
                        "openai",
                        profile_id="profile-owned-by-a",
                        current_user=current_user,
                    )
                )

        enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="owner")
        models_mock.assert_not_awaited()

    def test_provider_profile_enable_disable_delete_routes_authorize_against_owning_workspace(self) -> None:
        current_user = {"workspace_ids": {"ws-b"}, "workspace_roles": {"ws-b": "owner"}}
        cases = (
            (routes_connectors.enable_provider_profile_route, "enable_provider_profile"),
            (routes_connectors.disable_provider_profile_route, "disable_provider_profile"),
            (routes_connectors.delete_provider_profile_route, "delete_provider_profile"),
        )
        for route_fn, core_attr in cases:
            with self.subTest(route=route_fn.__name__):
                with patch.object(
                    routes_connectors.core,
                    "get_provider_profile_workspace_id",
                    return_value="ws-a",
                ) as profile_lookup_mock, patch.object(
                    routes_connectors,
                    "enforce_workspace_access",
                    side_effect=HTTPException(status_code=403, detail="Tenant is not accessible for this user."),
                ) as enforce_mock, patch.object(
                    routes_connectors.core,
                    core_attr,
                    new=AsyncMock(),
                ) as core_mock:
                    with self.assertRaises(HTTPException) as exc_info:
                        asyncio.run(route_fn("profile-owned-by-a", current_user=current_user))

                self.assertEqual(exc_info.exception.status_code, 403)
                profile_lookup_mock.assert_called_once_with("profile-owned-by-a")
                enforce_mock.assert_called_once_with(current_user, "ws-a", minimum_role="owner")
                core_mock.assert_not_awaited()

    def test_update_tool_contract_requires_platform_operator_access_not_just_any_owner(self) -> None:
        from server_modules.runtime_models import ToolContractUpdateRequest

        request = Request({"type": "http", "method": "PUT", "path": "/tools/contracts/browser__navigate", "query_string": b"", "headers": []})
        # An ordinary workspace owner -- exactly what `require_admin_api_key`
        # alone lets through -- must NOT be able to flip a tool platform-wide.
        current_user = {"auth_type": "bearer", "role": "owner", "auth_admin": False, "user_id": "u-b", "email": "owner-b@example.com"}
        with patch.object(
            routes_connectors,
            "current_user_has_auth_admin_access",
            return_value=False,
        ) as admin_check_mock, patch.object(
            routes_connectors.core,
            "update_tool_contract_state",
            new=AsyncMock(),
        ) as update_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(
                    routes_connectors.update_tool_contract(
                        request,
                        "browser__navigate",
                        ToolContractUpdateRequest(enabled=False),
                        current_user=current_user,
                    )
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        admin_check_mock.assert_called_once_with(current_user)
        update_mock.assert_not_awaited()

    def test_rotate_vault_key_route_requires_platform_operator_access_not_just_any_owner(self) -> None:
        from server_modules.runtime_models import VaultRotateKeyRequest

        current_user = {"auth_type": "bearer", "role": "owner", "auth_admin": False, "user_id": "u-b", "email": "owner-b@example.com"}
        with patch.object(
            routes_connectors,
            "current_user_has_auth_admin_access",
            return_value=False,
        ) as admin_check_mock, patch.object(
            routes_connectors.core,
            "rotate_vault_key",
            new=AsyncMock(),
        ) as rotate_mock:
            with self.assertRaises(HTTPException) as exc_info:
                asyncio.run(
                    routes_connectors.rotate_vault_key_route(
                        VaultRotateKeyRequest(new_passphrase="attacker-supplied-passphrase-1234"),
                        current_user=current_user,
                    )
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        admin_check_mock.assert_called_once_with(current_user)
        rotate_mock.assert_not_awaited()

    def test_previously_vulnerable_routes_now_carry_a_current_user_dependency(self) -> None:
        # Structural guard: each of these used to be registered with either
        # no per-route Depends() at all (auth only via a bare
        # `dependencies=[Depends(require_api_key)]`/`admin_deps` list, which
        # never compares the caller to the resource) or a wrapper that took
        # no current_user. Assert every one now resolves a `current_user`
        # dependant, which is what makes the enforce_workspace_access /
        # current_user_has_auth_admin_access call above possible at all.
        vulnerable_paths = {
            "/channels/telegram/send",
            "/channels/telegram/autopilot/test-message",
            "/providers/{provider}/probe",
            "/providers/{provider}/models",
            "/providers/profiles/{profile_id}/enable",
            "/providers/profiles/{profile_id}/disable",
            "/providers/profiles/{profile_id}",
            "/tools/contracts/{tool_id}",
            "/credentials/vault/rotate-key",
        }
        import inspect

        by_path: dict[str, list[APIRoute]] = {}
        for route in _api_routes():
            by_path.setdefault(route.path, []).append(route)
        for path in vulnerable_paths:
            routes = by_path.get(path)
            self.assertTrue(routes, f"Route {path} not found.")
            for route in routes:
                # Every wrapper now takes current_user as a real function
                # parameter (not just a bare dependencies=[...] list with no
                # capture), which is what makes an enforce_workspace_access /
                # current_user_has_auth_admin_access comparison possible at
                # all -- the vulnerable shape had no current_user reaching
                # the function body.
                sig = inspect.signature(route.endpoint)
                self.assertIn(
                    "current_user",
                    sig.parameters,
                    f"Route {path}'s endpoint {route.endpoint} does not take current_user as a parameter.",
                )

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
