import unittest

from server_modules.connectors.telegram_menu_service import TelegramMenuService


class TelegramMenuServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramMenuService:
        return TelegramMenuService(
            default_chat_prefix=overrides.pop("default_chat_prefix", "/empyralis"),
            show_buttons=overrides.pop("show_buttons", True),
            runtime_active_skills=overrides.pop(
                "runtime_active_skills",
                lambda scope_key, limit: [
                    {"id": "skill-a", "title": "Skill A"},
                    {"id": "skill-b", "title": "Skill B"},
                ],
            ),
        )

    def test_prefixed_command_uses_profile_prefix_when_required(self) -> None:
        service = self._make_service()
        profile = {"require_prefix": True, "prefix": "/ops"}
        self.assertEqual(service.prefixed_command(profile, "status"), "/ops status")
        self.assertEqual(service.prefixed_command({"require_prefix": False}, "status"), "status")

    def test_main_menu_keyboard_renders_prefixed_buttons(self) -> None:
        service = self._make_service()
        keyboard = service.menu_keyboard({"require_prefix": True, "prefix": "/ops"}, "main")
        self.assertEqual(keyboard["keyboard"][0][0]["text"], "/ops menu work")
        self.assertEqual(keyboard["keyboard"][3][0]["text"], "/ops approvals")

    def test_skills_menu_uses_runtime_active_skills(self) -> None:
        service = self._make_service()
        keyboard = service.menu_keyboard({"require_prefix": False}, "skills")
        flat = [item["text"] for row in keyboard["keyboard"] for item in row]
        self.assertIn("Skill: Skill A", flat)
        self.assertIn("Back to main", flat)

    def test_reply_keyboard_honors_show_buttons(self) -> None:
        hidden = self._make_service(show_buttons=False)
        self.assertEqual(hidden.reply_keyboard({}), {"remove_keyboard": True})


if __name__ == "__main__":
    unittest.main()
