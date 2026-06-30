"""Tests for server_modules.personal_channel_handler_registry."""

from __future__ import annotations

import unittest

from server_modules.personal_channel_handler_registry import (
    PersonalChannelHandler,
    PersonalChannelHandlerRegistry,
)


class _FakeHandler(PersonalChannelHandler):
    def __init__(self, channel_key: str, provider: str) -> None:
        self._channel_key = channel_key
        self._provider = provider

    @property
    def channel_key(self) -> str:
        return self._channel_key

    @property
    def provider(self) -> str:
        return self._provider

    def build_state_sync_payload(self, message):
        return {"synced": True, "channel": self._channel_key}

    def audit_action_prefix(self) -> str:
        return "fake"


class PersonalChannelHandlerRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = PersonalChannelHandlerRegistry()

    def test_register_and_get(self):
        h = _FakeHandler("whatsapp_personal", "whatsapp_baileys")
        self.registry.register(h)
        self.assertEqual(self.registry.get("whatsapp_personal"), h)

    def test_has_true(self):
        self.registry.register(_FakeHandler("whatsapp_personal", "wp"))
        self.assertTrue(self.registry.has("whatsapp_personal"))

    def test_has_false(self):
        self.assertFalse(self.registry.has("unknown"))

    def test_unknown_channel_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.registry.get("signal_personal")
        self.assertIn("signal_personal", str(ctx.exception))

    def test_duplicate_registration_raises(self):
        self.registry.register(_FakeHandler("whatsapp_personal", "wp"))
        with self.assertRaises(ValueError):
            self.registry.register(_FakeHandler("whatsapp_personal", "wp2"))

    def test_registered_keys(self):
        self.registry.register(_FakeHandler("whatsapp_personal", "wp"))
        self.registry.register(_FakeHandler("telegram_personal", "tg"))
        keys = self.registry.registered_keys()
        self.assertIn("whatsapp_personal", keys)
        self.assertIn("telegram_personal", keys)
        self.assertEqual(len(keys), 2)

    def test_build_state_sync_payload_dispatches(self):
        self.registry.register(_FakeHandler("whatsapp_personal", "wp"))
        payload = self.registry.build_state_sync_payload(
            channel_key="whatsapp_personal",
            message={"status": "connected"},
        )
        self.assertEqual(payload["synced"], True)

    def test_multiple_handler_dispatch(self):
        wa = _FakeHandler("whatsapp_personal", "whatsapp_baileys")
        tg = _FakeHandler("telegram_personal", "telegram_gramjs")
        self.registry.register(wa)
        self.registry.register(tg)
        self.assertEqual(self.registry.get("whatsapp_personal"), wa)
        self.assertEqual(self.registry.get("telegram_personal"), tg)

    def test_handler_properties(self):
        h = _FakeHandler("telegram_personal", "telegram_gramjs")
        self.assertEqual(h.channel_key, "telegram_personal")
        self.assertEqual(h.provider, "telegram_gramjs")
        self.assertEqual(h.audit_action_prefix(), "fake")  # _FakeHandler returns "fake"


if __name__ == "__main__":
    unittest.main()
