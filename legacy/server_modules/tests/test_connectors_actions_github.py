from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from server_modules import channel_preflight_service
from server_modules import connectors_actions


def _body_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _request_from_body(body: bytes, *, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/channels/github/webhook",
        "raw_path": b"/channels/github/webhook",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope, _receive)


def _signed_headers(body: bytes, secret: str, *, event_type: str = "push", delivery_id: str = "delivery-123") -> list[tuple[bytes, bytes]]:
    signature = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return [
        (b"x-hub-signature-256", signature.encode("utf-8")),
        (b"x-github-event", event_type.encode("utf-8")),
        (b"x-github-delivery", delivery_id.encode("utf-8")),
    ]


class GithubWebhookVerificationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        global connectors_actions

        connectors_actions = importlib.import_module("server_modules.connectors_actions")

    async def test_github_webhook_rejects_missing_secret_configuration(self):
        body = _body_bytes({"repository": {"full_name": "acme/api"}})
        request = _request_from_body(body, headers=_signed_headers(body, "secret-1"))

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": []}),
            patch("server_modules.connectors_actions.github_parse_inbound_event") as parse_event,
            patch.object(connectors_actions, "_append_channel_event", create=True) as append_event,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.github_events_webhook(request)

        self.assertEqual(exc_info.exception.status_code, 503)
        parse_event.assert_not_called()
        append_event.assert_not_called()

    def test_github_is_allowed_by_canonical_inbound_preflight(self):
        with (
            patch(
                "server_modules.channel_preflight_service.safe_mode_service.resolve_machine_policy_status",
                return_value={"kill_switch": {"active": False}},
            ),
            patch(
                "server_modules.channel_preflight_service.safe_mode_service.is_channel_disabled",
                return_value=False,
            ),
        ):
            channel_key, endpoint_key, message = channel_preflight_service.assert_inbound_allowed(
                tenant_id="tenant-default",
                workspace_id="default",
                channel_key="github",
                endpoint_key="acme/api",
                customer_message="GitHub issue opened in acme/api",
            )

        self.assertEqual(channel_key, "github")
        self.assertEqual(endpoint_key, "acme/api")
        self.assertEqual(message, "GitHub issue opened in acme/api")

    async def test_github_webhook_rejects_missing_signature_header(self):
        body = _body_bytes({"repository": {"full_name": "acme/api"}})
        request = _request_from_body(
            body,
            headers=[
                (b"x-github-event", b"push"),
                (b"x-github-delivery", b"delivery-123"),
            ],
        )
        connector_row = {"id": "cred-github", "provider": "github", "workspace_id": "default", "tenant_id": "tenant-default", "metadata": {"username": "acme"}}

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch("server_modules.connectors_actions.resolve_vault_credential", return_value={"webhook_secret": "secret-1"}),
            patch("server_modules.connectors_actions.github_parse_inbound_event") as parse_event,
            patch.object(connectors_actions, "_append_channel_event", create=True) as append_event,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.github_events_webhook(request)

        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "GitHub webhook signature header is required.")
        parse_event.assert_not_called()
        append_event.assert_not_called()

    async def test_github_webhook_rejects_invalid_signature(self):
        body = _body_bytes({"repository": {"full_name": "acme/api"}})
        request = _request_from_body(body, headers=_signed_headers(body, "wrong-secret"))
        connector_row = {
            "id": "cred-github",
            "provider": "github",
            "workspace_id": "default",
            "tenant_id": "tenant-default",
            "metadata": {"username": "acme"},
        }

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch("server_modules.connectors_actions.resolve_vault_credential", return_value={"webhook_secret": "secret-1"}),
            patch("server_modules.connectors_actions.github_parse_inbound_event") as parse_event,
            patch.object(connectors_actions, "_append_channel_event", create=True) as append_event,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.github_events_webhook(request)

        self.assertEqual(exc_info.exception.status_code, 401)
        parse_event.assert_not_called()
        append_event.assert_not_called()

    async def test_github_webhook_accepts_valid_signature(self):
        payload = {
            "repository": {"name": "api", "full_name": "acme/api", "owner": {"login": "acme"}},
            "sender": {"login": "octocat"},
        }
        body = _body_bytes(payload)
        request = _request_from_body(body, headers=_signed_headers(body, "secret-1"))
        connector_row = {"id": "cred-github", "provider": "github", "workspace_id": "default", "metadata": {"username": "other-owner"}}

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch("server_modules.connectors_actions.resolve_vault_credential", return_value={"webhook_secret": "secret-1"}),
            patch.object(connectors_actions, "_append_channel_event", None, create=True),
        ):
            result = await connectors_actions.github_events_webhook(request)

        self.assertEqual(result, {"ok": True, "handled": 0, "triggered": 0, "run_id": None})

    async def test_github_webhook_verified_event_appends_activity(self):
        payload = {
            "ref": "refs/heads/main",
            "before": "abc",
            "after": "def",
            "repository": {
                "name": "api",
                "full_name": "acme/api",
                "owner": {"login": "acme"},
            },
            "sender": {"login": "octocat"},
            "commits": [{"id": "1"}, {"id": "2"}],
            "head_commit": {"message": "Ship it"},
        }
        body = _body_bytes(payload)
        request = _request_from_body(body, headers=_signed_headers(body, "secret-1", delivery_id="delivery-456"))
        connector_row = {
            "id": "cred-github",
            "provider": "github",
            "workspace_id": "default",
            "tenant_id": "tenant-default",
            "metadata": {"username": "acme"},
        }
        appended: list[dict] = []

        def _append_event(**kwargs):
            appended.append(kwargs)
            return kwargs

        route_message = AsyncMock(return_value={"ok": True, "run_id": "run-github-1", "reply": "Tracking it."})

        with (
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [connector_row]}),
            patch(
                "server_modules.connectors_actions.resolve_vault_credential",
                return_value={"webhook_secret": "secret-1", "username": "acme"},
            ),
            patch.object(connectors_actions, "_append_channel_event", _append_event, create=True),
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
        ):
            result = await connectors_actions.github_events_webhook(request)

        self.assertEqual(result, {"ok": True, "handled": 1, "triggered": 1, "run_id": "run-github-1"})
        self.assertEqual(len(appended), 1)
        event = appended[0]
        self.assertEqual(event["channel"], "github")
        self.assertEqual(event["direction"], "inbound")
        self.assertEqual(event["event_type"], "push")
        self.assertEqual(event["text"], "Ship it")
        self.assertEqual(event["workspace_id"], "default")
        self.assertEqual(event["session_key"], "acme/api")
        self.assertEqual(event["message_id"], "delivery-456")
        self.assertEqual(event["trace_id"], "github:delivery-456")
        self.assertEqual(event["metadata"]["repository"], "acme/api")
        route_message.assert_awaited_once()
        kwargs = route_message.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "default")
        self.assertEqual(kwargs["channel_key"], "github")
        self.assertEqual(kwargs["endpoint_key"], "acme/api")
        self.assertIn("GitHub push in acme/api", kwargs["customer_message"])
        self.assertFalse(kwargs["allow_master_fallback"])
