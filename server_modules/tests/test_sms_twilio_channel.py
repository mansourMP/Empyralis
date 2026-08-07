"""Tests for the SMS-via-Twilio channel: transport (send_sms strips the
`whatsapp:` prefix), inbound webhook (parses + routes to the agent, rejects a
bad signature), and provisioning (the "not configured" env gate).

Mirrors test_whatsapp_transport_service.py and test_slack_connector.py.
"""

from __future__ import annotations

import unittest
from unittest import mock
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs

from fastapi import HTTPException
from starlette.requests import Request

from server_modules import connectors_actions
from server_modules import sms_twilio_provisioning_service as sms
from server_modules.connectors.whatsapp_transport_service import WhatsAppTransportService


def _request_from_body(body: bytes, *, headers: list[tuple[bytes, bytes]] | None = None) -> Request:
    async def _receive() -> dict:
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/channels/sms/twilio/webhook",
        "raw_path": b"/channels/sms/twilio/webhook",
        "query_string": b"",
        "headers": headers or [],
        "client": ("127.0.0.1", 54321),
        "server": ("127.0.0.1", 8001),
    }
    return Request(scope, _receive)


class _FakeHttpResponse:
    status = 201

    def __init__(self, raw: bytes = b'{"sid": "SM123"}') -> None:
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class SmsTransportTests(unittest.TestCase):
    def test_normalize_sms_number_strips_whatsapp_prefix(self) -> None:
        service = WhatsAppTransportService()
        self.assertEqual(service.normalize_sms_number("whatsapp:+15551230000"), "+15551230000")
        self.assertEqual(service.normalize_sms_number("+15551230000"), "+15551230000")
        self.assertEqual(service.normalize_sms_number(" +1 555 123 0000 "), "+15551230000")
        self.assertEqual(service.normalize_sms_number("*"), "*")
        self.assertEqual(service.normalize_sms_number(""), "")

    def test_send_sms_strips_whatsapp_prefix_on_the_wire(self) -> None:
        """The whole point of send_sms vs send_message: From/To go out as bare
        E.164 (no `whatsapp:`), so Twilio delivers over SMS, not WhatsApp."""
        service = WhatsAppTransportService()
        with mock.patch(
            "server_modules.connectors.whatsapp_transport_service.urlrequest.urlopen",
            return_value=_FakeHttpResponse(),
        ) as mocked_urlopen:
            payload = service.send_sms(
                account_sid="AC123",
                auth_token="token",
                from_number="whatsapp:+15550001111",
                to_number="+15552223333",
                body="hi over sms",
            )
        self.assertEqual(payload["sid"], "SM123")
        request = mocked_urlopen.call_args.args[0]
        self.assertIn("/Accounts/AC123/Messages.json", request.full_url)
        form = parse_qs(request.data.decode("utf-8"))
        self.assertEqual(form["From"], ["+15550001111"])
        self.assertEqual(form["To"], ["+15552223333"])
        self.assertNotIn("whatsapp:", request.data.decode("utf-8"))

    def test_send_sms_requires_credentials_and_numbers(self) -> None:
        service = WhatsAppTransportService()
        with self.assertRaisesRegex(RuntimeError, "account_sid/auth_token"):
            service.send_sms(account_sid="", auth_token="", from_number="+1", to_number="+2", body="x")
        with self.assertRaisesRegex(RuntimeError, "From/To numbers"):
            service.send_sms(account_sid="AC", auth_token="tok", from_number="", to_number="", body="x")


class SmsProvisioningGateTests(unittest.IsolatedAsyncioTestCase):
    def test_is_configured_reflects_env(self) -> None:
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": "AC", "TWILIO_AUTH_TOKEN": "tok"}, clear=False):
            self.assertTrue(sms.is_configured())
            self.assertIsNotNone(sms.platform_twilio_credentials())
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": ""}, clear=False):
            self.assertFalse(sms.is_configured())
            self.assertIsNone(sms.platform_twilio_credentials())

    def test_preflight_not_configured_is_safe(self) -> None:
        with patch.dict("os.environ", {"TWILIO_ACCOUNT_SID": "", "TWILIO_AUTH_TOKEN": ""}, clear=False):
            pf = sms.preflight()
        self.assertFalse(pf["configured"])
        self.assertEqual(pf["status"], "not_configured")
        self.assertEqual(pf["channel_key"], "sms_twilio")
        self.assertTrue(pf["requires_us_10dlc_registration"])

    async def test_assign_agent_sms_number_raises_when_unset(self) -> None:
        with patch.object(sms, "platform_twilio_credentials", return_value=None):
            with self.assertRaises(sms.SmsNotConfiguredError):
                await sms.assign_agent_sms_number(
                    agent_install_id="agent-1", workspace_id="default", tenant_id="tenant-default",
                )

    def test_send_platform_sms_raises_when_unset(self) -> None:
        with patch.object(sms, "platform_twilio_credentials", return_value=None):
            with self.assertRaises(sms.SmsNotConfiguredError):
                sms.send_platform_sms(from_number="+15550001111", to_number="+15552223333", body="hi")

    def test_send_platform_sms_delegates_to_transport_send_sms(self) -> None:
        with (
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch(
                "server_modules.connectors.whatsapp_transport_service.WhatsAppTransportService.send_sms",
                return_value={"sid": "SM999"},
            ) as send_sms_mock,
        ):
            result = sms.send_platform_sms(from_number="+15550001111", to_number="+15552223333", body="hello")
        self.assertEqual(result["sid"], "SM999")
        kwargs = send_sms_mock.call_args.kwargs
        self.assertEqual(kwargs["account_sid"], "AC")
        self.assertEqual(kwargs["auth_token"], "tok")
        self.assertEqual(kwargs["from_number"], "+15550001111")
        self.assertEqual(kwargs["to_number"], "+15552223333")

    async def test_assign_agent_sms_number_happy_path(self) -> None:
        with (
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms.bindings, "agent_install_in_scope", new=AsyncMock(return_value=True)),
            patch.object(sms, "search_available_numbers", new=AsyncMock(return_value=[{"phone_number": "+15550001111"}])),
            patch.object(sms, "purchase_number", new=AsyncMock(return_value={"phone_number": "+15550001111", "sid": "PN123"})),
            patch.object(sms, "store_sms_credential", return_value="cred-1"),
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock(return_value={"id": "achbind-1"})) as upsert,
        ):
            result = await sms.assign_agent_sms_number(
                agent_install_id="agent-1", workspace_id="default", tenant_id="tenant-default",
            )

        self.assertEqual(result["phone_number"], "+15550001111")
        self.assertEqual(result["phone_number_sid"], "PN123")
        self.assertEqual(result["endpoint_key"], "+15550001111")
        self.assertEqual(result["channel_key"], "sms")
        self.assertEqual(result["webhook_url"], "https://hook.test/channels/sms/twilio/webhook")
        kwargs = upsert.await_args.kwargs
        self.assertEqual(kwargs["channel_key"], "sms")
        self.assertTrue(kwargs["enabled"])
        self.assertEqual(kwargs["binding"]["endpoint_key"], "+15550001111")
        self.assertTrue(kwargs["binding"]["is_inbound_owner"])

    async def test_assign_agent_sms_number_rolls_back_credential_on_binding_failure(self) -> None:
        with (
            patch.object(sms, "platform_twilio_credentials", return_value={"account_sid": "AC", "auth_token": "tok"}),
            patch.object(sms, "webhook_base_url", return_value="https://hook.test"),
            patch.object(sms.bindings, "agent_install_in_scope", new=AsyncMock(return_value=True)),
            patch.object(sms, "search_available_numbers", new=AsyncMock(return_value=[{"phone_number": "+15550001111"}])),
            patch.object(sms, "purchase_number", new=AsyncMock(return_value={"phone_number": "+15550001111", "sid": "PN123"})),
            patch.object(sms, "store_sms_credential", return_value="cred-1"),
            patch.object(sms.bindings, "upsert_channel_binding", new=AsyncMock(side_effect=RuntimeError("db down"))),
            patch.object(sms, "delete_vault_credential_by_id", return_value=True) as delete_cred,
        ):
            with self.assertRaises(RuntimeError):
                await sms.assign_agent_sms_number(
                    agent_install_id="agent-1", workspace_id="default", tenant_id="tenant-default",
                )
        delete_cred.assert_called_once_with("cred-1")


class SmsInboundWebhookTests(unittest.IsolatedAsyncioTestCase):
    def _sms_row(self) -> dict:
        return {
            "id": "cred-sms",
            "provider": "sms_twilio",
            "workspace_id": "default",
            "tenant_id": "tenant-default",
            "metadata": {"phone_number": "+15550001111", "sms_endpoint_key": "+15550001111"},
        }

    async def test_webhook_returns_503_when_not_configured(self) -> None:
        request = _request_from_body(b"From=%2B15559998888&To=%2B15550001111&Body=hi")
        with patch(
            "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
            return_value=None,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.sms_twilio_webhook(request)
        self.assertEqual(exc_info.exception.status_code, 503)
        # MAN-293 (same leak shape): this route has no auth -- anyone who
        # POSTs to the webhook path sees this response -- so the 503 body
        # must never name the env vars the platform Twilio account needs
        # (sms.NOT_CONFIGURED_MESSAGE, which does name them, is for the
        # server log only now; see connectors_actions.sms_twilio_webhook).
        detail = str(exc_info.exception.detail or "")
        self.assertNotIn("TWILIO_ACCOUNT_SID", detail)
        self.assertNotIn("TWILIO_AUTH_TOKEN", detail)
        self.assertTrue(detail.strip())

    async def test_webhook_rejects_missing_signature(self) -> None:
        request = _request_from_body(b"From=%2B15559998888&To=%2B15550001111&Body=hi")
        with patch(
            "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
            return_value={"account_sid": "AC", "auth_token": "tok"},
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.sms_twilio_webhook(request)
        self.assertEqual(exc_info.exception.status_code, 401)

    async def test_webhook_rejects_invalid_signature(self) -> None:
        headers = [(b"x-twilio-signature", b"deadbeef")]
        request = _request_from_body(b"From=%2B15559998888&To=%2B15550001111&Body=hi", headers=headers)
        route_message = AsyncMock()
        with (
            patch(
                "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
                return_value={"account_sid": "AC", "auth_token": "tok"},
            ),
            patch(
                "server_modules.connectors.whatsapp_transport_service.WhatsAppTransportService.validate_webhook_signature",
                return_value=False,
            ),
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await connectors_actions.sms_twilio_webhook(request)
        self.assertEqual(exc_info.exception.status_code, 403)
        route_message.assert_not_awaited()

    async def test_webhook_parses_and_routes_to_agent(self) -> None:
        """Gate 1: a NUMBER already paired/linked to the workspace (an active
        channel_pairing_service link for provider="sms") still reaches the
        agent turn exactly as before — Gate 1 must not block a known sender."""
        headers = [(b"x-twilio-signature", b"validsig")]
        request = _request_from_body(
            b"From=%2B15559998888&To=%2B15550001111&Body=hello+agent&MessageSid=SM42",
            headers=headers,
        )
        route_message = AsyncMock(return_value={"ok": True, "run_id": "run-1", "reply": "Working on it."})
        pairing_service = mock.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": True,
            "status": "linked",
            "workspace_id": "default",
        }
        with (
            patch(
                "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
                return_value={"account_sid": "AC", "auth_token": "tok"},
            ),
            patch(
                "server_modules.connectors.whatsapp_transport_service.WhatsAppTransportService.validate_webhook_signature",
                return_value=True,
            ),
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [self._sms_row()]}),
            patch("server_modules.connectors_actions._append_channel_event", return_value=None) as append_event,
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=pairing_service,
            ),
        ):
            response = await connectors_actions.sms_twilio_webhook(request)

        # Reply rides back as TwiML <Message> — the primary inbound reply path.
        body = response.body.decode("utf-8")
        self.assertIn("<Message>Working on it.</Message>", body)
        append_event.assert_called_once()
        route_message.assert_awaited_once()
        kwargs = route_message.await_args.kwargs
        self.assertEqual(kwargs["workspace_id"], "default")
        self.assertEqual(kwargs["tenant_id"], "tenant-default")
        self.assertEqual(kwargs["channel_key"], "sms")
        self.assertEqual(kwargs["endpoint_key"], "+15550001111")
        self.assertEqual(kwargs["customer_message"], "hello agent")
        self.assertEqual(kwargs["actor_id"], "+15559998888")
        self.assertFalse(kwargs["allow_master_fallback"])
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="sms",
            external_subject="+15559998888",
            workspace_id="default",
            message_text="hello agent",
        )

    async def test_webhook_unpaired_number_never_routes_to_agent(self) -> None:
        """Gate 1 (THE ACTUAL FIX): before this change, any phone number that
        texted a bound Twilio number reached the agent turn directly — no
        pairing, no allowlist (CHANNEL-GATEWAY-PLAN.md §5a). An unpaired
        number must now get a pairing prompt back over SMS and must NEVER
        reach route_inbound_channel_message (the SMS chokepoint into
        execute_sage_turn)."""
        headers = [(b"x-twilio-signature", b"validsig")]
        request = _request_from_body(
            b"From=%2B15559998888&To=%2B15550001111&Body=hello+agent&MessageSid=SM43",
            headers=headers,
        )
        route_message = AsyncMock()
        pairing_service = mock.MagicMock()
        pairing_service.authorize_channel_message.return_value = {
            "authorized": False,
            "status": "pairing_required",
            "connect_url": "https://app.empyralis.test/continue?source=channel_connect&channel=sms",
            "reply_text": "This SMS identity is not linked to Empyralis yet. Open this link to connect it: https://app.empyralis.test/continue?source=channel_connect&channel=sms",
        }
        with (
            patch(
                "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
                return_value={"account_sid": "AC", "auth_token": "tok"},
            ),
            patch(
                "server_modules.connectors.whatsapp_transport_service.WhatsAppTransportService.validate_webhook_signature",
                return_value=True,
            ),
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [self._sms_row()]}),
            patch("server_modules.connectors_actions._append_channel_event", return_value=None),
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
            patch(
                "server_modules.channel_pairing_service.get_channel_pairing_service",
                return_value=pairing_service,
            ),
        ):
            response = await connectors_actions.sms_twilio_webhook(request)

        route_message.assert_not_awaited()
        body = response.body.decode("utf-8")
        self.assertIn("not linked to Empyralis", body)
        pairing_service.authorize_channel_message.assert_called_once_with(
            provider="sms",
            external_subject="+15559998888",
            workspace_id="default",
            message_text="hello agent",
        )

    async def test_webhook_unknown_number_replies_empty_twiml(self) -> None:
        headers = [(b"x-twilio-signature", b"validsig")]
        # `To` does not match any provisioned sms_twilio credential.
        request = _request_from_body(
            b"From=%2B15559998888&To=%2B19999999999&Body=hello",
            headers=headers,
        )
        route_message = AsyncMock()
        with (
            patch(
                "server_modules.sms_twilio_provisioning_service.platform_twilio_credentials",
                return_value={"account_sid": "AC", "auth_token": "tok"},
            ),
            patch(
                "server_modules.connectors.whatsapp_transport_service.WhatsAppTransportService.validate_webhook_signature",
                return_value=True,
            ),
            patch("server_modules.connectors_actions.load_vault", return_value={"credentials": [self._sms_row()]}),
            patch("server_modules.agent_channel_router.route_inbound_channel_message", new=route_message),
        ):
            response = await connectors_actions.sms_twilio_webhook(request)

        body = response.body.decode("utf-8")
        self.assertIn("<Response></Response>", body)
        route_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
