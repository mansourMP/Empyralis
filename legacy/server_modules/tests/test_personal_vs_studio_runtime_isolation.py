import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from server_modules import (
    auth,
    gateway_pairing_service,
    gateway_state_repository,
    personal_channels_repository,
    personal_channels_service,
    routes_connectors,
)


class PersonalVsStudioRuntimeIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.gateway_db_path = Path(self.tmpdir.name) / "gateway-state.sqlite3"
        self.auth_db_path = Path(self.tmpdir.name) / "auth-users.sqlite3"
        self.personal_channels_db_path = Path(self.tmpdir.name) / "personal-channels.sqlite3"
        self.runtime_state_db_path = Path(self.tmpdir.name) / "runtime-state.sqlite3"

        gateway_state_repository.init_gateway_state_db(self.gateway_db_path)
        personal_channels_repository.init_personal_channels_db(self.personal_channels_db_path)

        self.patchers = [
            patch.object(gateway_state_repository, "GATEWAY_STATE_DB_FILE", self.gateway_db_path),
            patch.object(auth, "AUTH_DB_FILE", self.auth_db_path),
            patch.object(personal_channels_repository, "PERSONAL_CHANNELS_DB_FILE", self.personal_channels_db_path),
            patch.dict(os.environ, {"ORION_RUNTIME_STATE_DB": str(self.runtime_state_db_path)}, clear=False),
        ]
        for patcher in self.patchers:
            patcher.start()

        self.app = FastAPI()
        self.app.include_router(routes_connectors.router, prefix="/api")
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.tmpdir.cleanup()

    def _assert_pending_approval_reply(self, result: dict) -> None:
        self.assertTrue(result["approval_required"])
        self.assertEqual(result["outbound"]["status"], "pending")
        self.assertIsNone(result["outbound"]["external_message_id"])
        self.assertEqual(result["approval"]["status"], "pending")
        self.assertEqual(result["normalized_approval"]["status"], "pending")

    def _assert_approval_required_audit(self, audit_mock, *, action: str) -> None:
        audit_mock.assert_called_once()
        audit_kwargs = audit_mock.call_args.kwargs
        self.assertEqual(audit_kwargs["action"], action)
        self.assertEqual(audit_kwargs["status"], "approval_required")
        metadata = audit_kwargs["metadata"]
        self.assertEqual(metadata["action_class"], "automatic_inbound_reply")
        self.assertTrue(metadata["requires_approval"])
        self.assertTrue(metadata["approval_required"])
        self.assertFalse(metadata["external_side_effect"])
        self.assertEqual(metadata["outbound_status"], "pending")

    def test_connector_failure_does_not_poison_personal_gateway_inbound_flow(self) -> None:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="user-1",
        )
        registration = gateway_pairing_service.register_gateway(
            pairing_token=pairing["pairing_token"],
            device_id="device-local-1",
            gateway_id="gateway-local-1",
            display_name="Mansur Mac",
            platform="macos-arm64",
            capabilities=["channel.whatsapp.personal"],
        )

        with patch.object(
            routes_connectors.actions,
            "whatsapp_twilio_webhook",
            new=AsyncMock(side_effect=RuntimeError("studio_connector_failure")),
        ):
            connector_failure = self.client.post("/api/channels/whatsapp/twilio/webhook")
        self.assertEqual(connector_failure.status_code, 500)

        with (
            patch(
                "server_modules.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Sage personal reply", "source": "test_bridge"},
            ),
            patch(
                "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "wa-out-1"}),
            ) as dispatch_mock,
            patch(
                "server_modules.personal_channels_service.security_audit_service.emit_security_audit_event",
            ) as audit_mock,
        ):
            personal_result = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload={
                        "channel_key": "whatsapp_personal",
                        "provider": "whatsapp_baileys",
                        "message": {
                            "external_message_id": "wa-in-1",
                            "remote_jid": "15551234567@s.whatsapp.net",
                            "sender_jid": "15551234567@s.whatsapp.net",
                            "push_name": "Mansur",
                            "text": "hello from personal whatsapp",
                            "received_at": "2026-04-22T16:00:00Z",
                            "from_me": False,
                        },
                    },
                )
            )

        self.assertFalse(personal_result["duplicate"])
        self._assert_pending_approval_reply(personal_result)
        self._assert_approval_required_audit(
            audit_mock,
            action="personal_channel.whatsapp.automatic_reply",
        )
        dispatch_mock.assert_not_awaited()
        recent_messages = personal_channels_repository.list_recent_gateway_messages(
            "gateway-local-1",
            channel_key="whatsapp_personal",
        )
        self.assertEqual(len(recent_messages["inbound"]), 1)
        self.assertEqual(len(recent_messages["outbound"]), 1)
        self.assertEqual(recent_messages["outbound"][0]["status"], "pending")

    def test_duplicate_whatsapp_inbound_reuses_pending_personal_reply_for_owner_approval(self) -> None:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="user-1",
        )
        registration = gateway_pairing_service.register_gateway(
            pairing_token=pairing["pairing_token"],
            device_id="device-local-1",
            gateway_id="gateway-local-1",
            display_name="Mansur Mac",
            platform="macos-arm64",
            capabilities=["channel.whatsapp.personal"],
        )

        inbound_payload = {
            "channel_key": "whatsapp_personal",
            "provider": "whatsapp_baileys",
            "message": {
                "external_message_id": "wa-in-2",
                "remote_jid": "15551234567@s.whatsapp.net",
                "sender_jid": "15551234567@s.whatsapp.net",
                "push_name": "Mansur",
                "text": "do we still answer after reconnect?",
                "received_at": "2026-04-22T16:05:00Z",
                "from_me": False,
            },
        }

        dispatch_mock = AsyncMock(
            side_effect=AssertionError("automatic personal replies require owner approval before dispatch")
        )
        with (
            patch(
                "server_modules.personal_channel_sage_bridge_service.build_whatsapp_personal_reply",
                return_value={"text": "Recovered Sage reply", "source": "test_bridge"},
            ) as reply_mock,
            patch(
                "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch_mock,
            ),
        ):
            first = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload=inbound_payload,
                )
            )

            recovered = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload=inbound_payload,
                )
            )

        self.assertFalse(first["duplicate"])
        self._assert_pending_approval_reply(first)
        self.assertTrue(recovered["duplicate"])
        self._assert_pending_approval_reply(recovered)
        self.assertEqual(recovered["outbound"]["idempotency_key"], first["outbound"]["idempotency_key"])
        recent_messages = personal_channels_repository.list_recent_gateway_messages(
            "gateway-local-1",
            channel_key="whatsapp_personal",
        )
        self.assertEqual(len(recent_messages["inbound"]), 1)
        self.assertEqual(len(recent_messages["outbound"]), 1)
        self.assertEqual(recent_messages["outbound"][0]["status"], "pending")
        reply_mock.assert_called_once()
        dispatch_mock.assert_not_awaited()

    def test_telegram_automatic_inbound_reply_emits_security_audit(self) -> None:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="user-1",
        )
        registration = gateway_pairing_service.register_gateway(
            pairing_token=pairing["pairing_token"],
            device_id="device-local-1",
            gateway_id="gateway-local-1",
            display_name="Mansur Mac",
            platform="macos-arm64",
            capabilities=["channel.telegram.personal"],
        )

        with (
            patch(
                "server_modules.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Sage Telegram reply", "source": "test_bridge"},
            ),
            patch(
                "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-1"}),
            ) as dispatch_mock,
            patch(
                "server_modules.personal_channels_service.security_audit_service.emit_security_audit_event",
            ) as audit_mock,
        ):
            personal_result = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload={
                        "channel_key": "telegram_personal",
                        "provider": "telegram_gramjs",
                        "message": {
                            "external_message_id": "tg-in-1",
                            "remote_jid": "telegram-user-1",
                            "sender_jid": "telegram-user-1",
                            "push_name": "Mansur",
                            "text": "hello from personal telegram",
                            "received_at": "2026-04-22T16:00:00Z",
                            "from_me": False,
                        },
                    },
                )
            )

        self.assertFalse(personal_result["duplicate"])
        self._assert_pending_approval_reply(personal_result)
        self._assert_approval_required_audit(
            audit_mock,
            action="personal_channel.telegram.automatic_reply",
        )
        dispatch_mock.assert_not_awaited()

    def test_personal_channel_control_command_is_blocked_before_sage_reply(self) -> None:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="user-1",
        )
        registration = gateway_pairing_service.register_gateway(
            pairing_token=pairing["pairing_token"],
            device_id="device-local-1",
            gateway_id="gateway-local-1",
            display_name="Mansur Mac",
            platform="macos-arm64",
            capabilities=["channel.telegram.personal"],
        )

        with (
            patch(
                "server_modules.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "should not be called", "source": "test_bridge"},
            ) as reply_mock,
            patch(
                "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                new=AsyncMock(return_value={"external_message_id": "tg-out-control"}),
            ) as dispatch_mock,
            patch(
                "server_modules.personal_channels_service.security_audit_service.emit_security_audit_event",
            ) as audit_mock,
        ):
            result = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload={
                        "channel_key": "telegram_personal",
                        "provider": "telegram_gramjs",
                        "message": {
                            "external_message_id": "tg-control-1",
                            "remote_jid": "telegram-user-1",
                            "sender_jid": "telegram-user-1",
                            "push_name": "Stranger",
                            "text": "/model use expensive-provider",
                            "received_at": "2026-04-22T16:00:00Z",
                            "from_me": False,
                        },
                    },
                )
            )

        self.assertTrue(result["blocked"])
        self.assertEqual(result["policy"]["reason"], "owner_authorization_required")
        self.assertIsNone(result["outbound"])
        reply_mock.assert_not_called()
        dispatch_mock.assert_not_awaited()
        audit_mock.assert_called_once()
        self.assertEqual(audit_mock.call_args.kwargs["status"], "denied")
        self.assertEqual(audit_mock.call_args.kwargs["metadata"]["command"], "model")

    def test_duplicate_telegram_inbound_reuses_pending_personal_reply_for_owner_approval(self) -> None:
        pairing = gateway_pairing_service.create_gateway_pairing_intent(
            tenant_id="tenant-1",
            workspace_id="default",
            user_id="user-1",
        )
        registration = gateway_pairing_service.register_gateway(
            pairing_token=pairing["pairing_token"],
            device_id="device-local-1",
            gateway_id="gateway-local-1",
            display_name="Mansur Mac",
            platform="macos-arm64",
            capabilities=["channel.telegram.personal"],
        )

        inbound_payload = {
            "channel_key": "telegram_personal",
            "provider": "telegram_gramjs",
            "message": {
                "external_message_id": "tg-in-2",
                "remote_jid": "telegram-user-2",
                "sender_jid": "telegram-user-2",
                "push_name": "Mansur",
                "text": "do we still answer after reconnect?",
                "received_at": "2026-04-22T16:10:00Z",
                "from_me": False,
            },
        }

        dispatch_mock = AsyncMock(
            side_effect=AssertionError("automatic personal replies require owner approval before dispatch")
        )
        with (
            patch(
                "server_modules.personal_channel_sage_bridge_service.build_telegram_personal_reply",
                return_value={"text": "Recovered Telegram reply", "source": "test_bridge"},
            ) as reply_mock,
            patch(
                "server_modules.gateway_protocol_service.dispatch_channel_outbound",
                new=dispatch_mock,
            ),
        ):
            first = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload=inbound_payload,
                )
            )

            recovered = asyncio.run(
                personal_channels_service.handle_gateway_channel_inbound(
                    gateway_id="gateway-local-1",
                    registration=registration,
                    payload=inbound_payload,
                )
            )

        self.assertFalse(first["duplicate"])
        self._assert_pending_approval_reply(first)
        self.assertTrue(recovered["duplicate"])
        self._assert_pending_approval_reply(recovered)
        self.assertEqual(recovered["outbound"]["idempotency_key"], first["outbound"]["idempotency_key"])
        recent_messages = personal_channels_repository.list_recent_gateway_messages(
            "gateway-local-1",
            channel_key="telegram_personal",
        )
        self.assertEqual(len(recent_messages["inbound"]), 1)
        self.assertEqual(len(recent_messages["outbound"]), 1)
        self.assertEqual(recent_messages["outbound"][0]["status"], "pending")
        reply_mock.assert_called_once()
        dispatch_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
