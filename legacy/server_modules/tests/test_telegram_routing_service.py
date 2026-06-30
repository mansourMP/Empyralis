import unittest

from server_modules.connectors.telegram_routing_service import TelegramRoutingService


class TelegramRoutingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = {
            "prefix": "/orion",
            "require_prefix": False,
            "allow_free_text": True,
            "allow_status": True,
            "allow_help": True,
        }
        self.service = TelegramRoutingService(
            default_chat_prefix="/empyralis",
            quick_goal_templates={"project update": "Give me a concise update."},
            menu_goal_templates={"today priorities": "Set my priorities."},
            normalize_profile_field=lambda raw: {"exam": "exam", "project": "project", "prefs": "preferences"}.get(raw.strip().lower().lstrip("/"), ""),
            select_skill_from_text=lambda text: {"id": "crm", "title": "CRM", "intent": "Handle CRM work"} if text.strip().lower() in {"crm", "skill: crm"} else None,
            skill_goal_builder=lambda skill: f"Apply skill {skill['id']}",
        )

    def test_strip_prefix_handles_direct_and_colon_forms(self) -> None:
        self.assertEqual(self.service.strip_prefix("/orion run inbox", "/orion"), {"matched": True, "body": "run inbox"})
        self.assertEqual(self.service.strip_prefix("/orion: run inbox", "/orion"), {"matched": True, "body": "run inbox"})

    def test_route_message_handles_profile_and_skill_commands(self) -> None:
        routed = self.service.route_message("me set exam algebra review", self.profile)
        self.assertEqual(routed["action"], "profile_set")
        self.assertEqual(routed["field"], "exam")

        start_routed = self.service.route_message("/start youtube-health", self.profile)
        self.assertEqual(start_routed["action"], "onboard_start")
        self.assertEqual(start_routed["payload"], "youtube-health")

        skill_routed = self.service.route_message("skill crm", self.profile)
        self.assertEqual(skill_routed["action"], "run")
        self.assertEqual(skill_routed["goal"], "Apply skill crm")

    def test_route_message_uses_templates_and_free_text(self) -> None:
        template_routed = self.service.route_message("Project update", self.profile)
        self.assertEqual(template_routed["action"], "run")
        self.assertIn("concise update", template_routed["goal"])

        free_text = self.service.route_message("review my roadmap", self.profile)
        self.assertEqual(free_text, {"action": "run", "goal": "review my roadmap"})

    def test_help_text_and_explicit_run_detection(self) -> None:
        help_text = self.service.help_text({"prefix": "/orion", "require_prefix": True, "allow_status": True, "allow_help": True, "allow_free_text": False})
        self.assertIn("/orion run <goal>", help_text)
        self.assertIn("Prefix mode is on", help_text)
        self.assertTrue(self.service.is_explicit_run_command("run summarize"))
        self.assertTrue(self.service.is_explicit_run_command("/task summarize"))
        self.assertFalse(self.service.is_explicit_run_command("summarize"))
