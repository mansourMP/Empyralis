import unittest

from server_modules.connectors.autopilot_profile_service import AutopilotProfileService


class AutopilotProfileServiceTests(unittest.TestCase):
    def _make_service(self) -> AutopilotProfileService:
        telegram_catalog = {
            "assistant": {"label": "Assistant", "description": "General", "allow_free_text": True, "allow_status": True, "allow_help": True},
            "strict": {"label": "Strict", "description": "Strict", "allow_free_text": False, "allow_status": True, "allow_help": True},
        }
        whatsapp_catalog = {
            "assistant": {"label": "Assistant", "description": "General", "allow_free_text": True, "allow_status": True, "allow_help": True},
            "ops": {"label": "Ops", "description": "Ops", "allow_free_text": False, "allow_status": True, "allow_help": False},
        }
        return AutopilotProfileService(
            default_chat_prefix="/empyralis",
            bool_from_any=lambda value, default=False: default if value is None else str(value).lower() in {"1", "true", "yes", "on"},
            telegram_default_profile="assistant",
            telegram_default_prefix="/empyralis",
            telegram_default_require_prefix=False,
            telegram_profile_catalog=telegram_catalog,
            whatsapp_default_profile="assistant",
            whatsapp_default_prefix="/empyralis",
            whatsapp_default_require_prefix=True,
            whatsapp_profile_catalog=whatsapp_catalog,
        )

    def test_resolve_telegram_profile_falls_back_to_default(self) -> None:
        service = self._make_service()
        profile = service.resolve_telegram_profile({"metadata": {"autopilot_profile": "missing"}})
        self.assertEqual(profile["id"], "assistant")
        self.assertTrue(profile["allow_free_text"])
        self.assertEqual(profile["status"], "degraded")
        self.assertEqual(profile["issue_code"], "telegram_autopilot_profile_missing")

    def test_resolve_whatsapp_profile_uses_channel_override(self) -> None:
        service = self._make_service()
        profile = service.resolve_whatsapp_profile(
            {"metadata": {"autopilot_profile_whatsapp": "ops", "autopilot_prefix_whatsapp": "/ops", "autopilot_require_prefix_whatsapp": "false"}}
        )
        self.assertEqual(profile["id"], "ops")
        self.assertEqual(profile["prefix"], "/ops")
        self.assertFalse(profile["require_prefix"])
        self.assertFalse(profile["allow_free_text"])
        self.assertEqual(profile["status"], "live")

    def test_resolve_whatsapp_profile_falls_back_to_generic_profile_key(self) -> None:
        service = self._make_service()
        profile = service.resolve_whatsapp_profile({"metadata": {"autopilot_profile": "ops"}})
        self.assertEqual(profile["id"], "ops")
        self.assertEqual(profile["status"], "live")

    def test_whatsapp_help_text_respects_profile_flags(self) -> None:
        service = self._make_service()
        text = service.whatsapp_help_text({"prefix": "/ops", "allow_status": True, "allow_help": False, "allow_free_text": False})
        self.assertIn("/ops status", text)
        self.assertNotIn("/ops help", text)
        self.assertIn("Plain text is ignored", text)

    def test_resolve_telegram_profile_handles_empty_catalog_without_crashing(self) -> None:
        service = AutopilotProfileService(
            default_chat_prefix="/empyralis",
            bool_from_any=lambda value, default=False: default if value is None else str(value).lower() in {"1", "true", "yes", "on"},
            telegram_default_profile="assistant",
            telegram_default_prefix="/empyralis",
            telegram_default_require_prefix=False,
            telegram_profile_catalog={},
            whatsapp_default_profile="assistant",
            whatsapp_default_prefix="/empyralis",
            whatsapp_default_require_prefix=True,
            whatsapp_profile_catalog={},
        )

        profile = service.resolve_telegram_profile({})

        self.assertEqual(profile["id"], "assistant")
        self.assertEqual(profile["status"], "setup_needed")
        self.assertEqual(profile["issue_code"], "telegram_autopilot_profile_catalog_missing")
        self.assertFalse(profile["allow_free_text"])

    def test_resolve_telegram_profile_degrades_when_default_profile_missing(self) -> None:
        service = AutopilotProfileService(
            default_chat_prefix="/empyralis",
            bool_from_any=lambda value, default=False: default if value is None else str(value).lower() in {"1", "true", "yes", "on"},
            telegram_default_profile="assistant",
            telegram_default_prefix="/empyralis",
            telegram_default_require_prefix=False,
            telegram_profile_catalog={"ops": {"label": "Ops", "allow_free_text": False, "allow_status": True, "allow_help": True}},
            whatsapp_default_profile="assistant",
            whatsapp_default_prefix="/empyralis",
            whatsapp_default_require_prefix=True,
            whatsapp_profile_catalog={},
        )

        profile = service.resolve_telegram_profile({})

        self.assertEqual(profile["id"], "ops")
        self.assertEqual(profile["status"], "degraded")
        self.assertEqual(profile["issue_code"], "telegram_autopilot_default_profile_missing")


if __name__ == "__main__":
    unittest.main()
