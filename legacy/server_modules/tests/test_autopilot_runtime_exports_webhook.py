from __future__ import annotations

import unittest
from unittest.mock import patch
import os

from starlette.requests import Request

from server_modules.connectors import autopilot_runtime_exports as exports


def _request(*, body: bytes, path: str, query_string: bytes = b"", headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string,
        "headers": headers or [],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope, _receive)


class _FakeRuntimeFacade:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def handle_whatsapp_webhook(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, **kwargs}

    async def handle_telegram_webhook(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True, **kwargs}


class _FakeShellService:
    def __init__(self, runtime_facade: _FakeRuntimeFacade) -> None:
        self._runtime_facade = runtime_facade

    def runtime_facade_service(self):
        return self._runtime_facade


class AutopilotRuntimeExportsWebhookTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_whatsapp_twilio_webhook_extracts_query_and_header_secrets(self):
        runtime_facade = _FakeRuntimeFacade()
        request = _request(
            body=b"Body=hello",
            path="/channels/whatsapp/twilio/webhook",
            query_string=b"secret=query-secret",
            headers=[
                (b"x-orion-webhook-secret", b" header-secret "),
                (b"x-twilio-signature", b" signature-1 "),
            ],
        )

        with patch.dict(os.environ, {"ORION_WHATSAPP_AUTOPILOT_PUBLIC_BASE_URL": "https://public.example.com"}, clear=False):
            with patch.object(exports, "_AUTOPILOT_CONNECTOR_SHELL_SERVICE", _FakeShellService(runtime_facade)):
                result = await exports.handle_whatsapp_twilio_webhook(request)

        self.assertTrue(result["ok"])
        self.assertEqual(len(runtime_facade.calls), 1)
        self.assertEqual(runtime_facade.calls[0]["query_secret"], "query-secret")
        self.assertEqual(runtime_facade.calls[0]["header_secret"], "header-secret")
        self.assertEqual(runtime_facade.calls[0]["twilio_signature"], "signature-1")
        self.assertEqual(runtime_facade.calls[0]["request_url"], "https://public.example.com/channels/whatsapp/twilio/webhook?secret=query-secret")
        self.assertEqual(runtime_facade.calls[0]["raw_body"], b"Body=hello")

    async def test_handle_telegram_webhook_extracts_secret_header_and_connector(self):
        runtime_facade = _FakeRuntimeFacade()
        request = _request(
            body=b'{"update_id": 9}',
            path="/channels/telegram/webhook/tg-1",
            headers=[(b"x-telegram-bot-api-secret-token", b" telegram-secret ")],
        )

        with patch.object(exports, "_AUTOPILOT_CONNECTOR_SHELL_SERVICE", _FakeShellService(runtime_facade)):
            result = await exports.handle_telegram_webhook(request, "tg-1")

        self.assertTrue(result["ok"])
        self.assertEqual(len(runtime_facade.calls), 1)
        self.assertEqual(runtime_facade.calls[0]["connector_id"], "tg-1")
        self.assertEqual(runtime_facade.calls[0]["header_secret"], "telegram-secret")
        self.assertEqual(runtime_facade.calls[0]["raw_body"], b'{"update_id": 9}')
