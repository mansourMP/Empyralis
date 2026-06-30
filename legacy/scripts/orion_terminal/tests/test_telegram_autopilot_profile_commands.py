import unittest
import importlib.util
from pathlib import Path

try:
    module_path = Path(__file__).resolve().parents[3] / "server_modules" / "autopilot_connectors.py"
    spec = importlib.util.spec_from_file_location("orion_test_autopilot_connectors", module_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load module spec: {module_path}")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    ac = None
    _IMPORT_ERROR = exc


class TelegramAutopilotProfileCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        if ac is None:
            self.skipTest(f"server_modules dependencies unavailable: {_IMPORT_ERROR}")
        self.profile = {
            "prefix": "/orion",
            "require_prefix": False,
            "allow_free_text": True,
            "allow_help": True,
            "allow_status": True,
        }

    def test_route_profile_show(self) -> None:
        routed = ac._telegram_route_message("me", self.profile)
        self.assertEqual(routed.get("action"), "profile_show")

    def test_route_profile_set(self) -> None:
        routed = ac._telegram_route_message("me set exam algebra review", self.profile)
        self.assertEqual(routed.get("action"), "profile_set")
        self.assertEqual(routed.get("field"), "exam")
        self.assertEqual(routed.get("value"), "algebra review")

    def test_route_profile_clear(self) -> None:
        routed = ac._telegram_route_message("me clear project", self.profile)
        self.assertEqual(routed.get("action"), "profile_clear")
        self.assertEqual(routed.get("field"), "project")

    def test_route_quick_goal_template(self) -> None:
        routed = ac._telegram_route_message("Project update", self.profile)
        self.assertEqual(routed.get("action"), "run")
        self.assertIn("top priorities", str(routed.get("goal") or "").lower())

    def test_build_goal_with_context_keeps_request_when_empty(self) -> None:
        request = "Plan my day"
        merged = ac._telegram_build_goal_with_profile(request, {"about": "", "project": "", "exam": "", "preferences": ""})
        self.assertEqual(merged, request)

    def test_build_goal_with_context_embeds_fields(self) -> None:
        merged = ac._telegram_build_goal_with_profile(
            "What should I do next?",
            {"project": "launching MVP", "exam": "math finals next week"},
        )
        self.assertIn("User context", merged)
        self.assertIn("launching MVP", merged)
        self.assertIn("User request", merged)

    def test_extract_message_photo_attachment(self) -> None:
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "chat": {"id": 123},
                "from": {"id": 456, "is_bot": False},
                "caption": "check this",
                "photo": [
                    {"file_id": "small", "width": 50, "height": 50, "file_size": 1000},
                    {"file_id": "large", "width": 1000, "height": 1000, "file_size": 100000},
                ],
            },
        }
        message = ac._telegram_extract_message(update)
        self.assertIsInstance(message, dict)
        attachments = message.get("attachments") if isinstance(message, dict) else []
        self.assertIsInstance(attachments, list)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(str(attachments[0].get("file_id")), "large")

    def test_build_goal_with_attachments_includes_paths(self) -> None:
        merged = ac._telegram_build_goal_with_attachments(
            "Help me understand this screenshot",
            [{"relative_path": ".orion-media/telegram/default/1932/img1.jpg", "mime_type": "image/jpeg"}],
        )
        self.assertIn("Help me understand this screenshot", merged)
        self.assertIn(".orion-media/telegram/default/1932/img1.jpg", merged)

    def test_route_study_menu_button(self) -> None:
        routed = ac._telegram_route_message("Study menu", self.profile)
        self.assertEqual(routed.get("action"), "menu_study")

    def test_route_work_menu_button(self) -> None:
        routed = ac._telegram_route_message("Work menu", self.profile)
        self.assertEqual(routed.get("action"), "menu_study")

    def test_route_back_to_main_button(self) -> None:
        routed = ac._telegram_route_message("Back to main", self.profile)
        self.assertEqual(routed.get("action"), "menu_main")

    def test_route_orion_keyword_opens_main_menu(self) -> None:
        routed = ac._telegram_route_message("orion", self.profile)
        self.assertEqual(routed.get("action"), "menu_main")

    def test_route_orion_home_opens_main_menu(self) -> None:
        routed = ac._telegram_route_message("orion home", self.profile)
        self.assertEqual(routed.get("action"), "menu_main")

    def test_route_home_command_opens_main_menu(self) -> None:
        routed = ac._telegram_route_message("home", self.profile)
        self.assertEqual(routed.get("action"), "menu_main")

    def test_route_commands_alias_maps_to_help(self) -> None:
        routed = ac._telegram_route_message("commands", self.profile)
        self.assertEqual(routed.get("action"), "help")

    def test_route_orion_run_extracts_goal(self) -> None:
        routed = ac._telegram_route_message("orion run summarize my priorities", self.profile)
        self.assertEqual(routed.get("action"), "run")
        self.assertEqual(routed.get("goal"), "summarize my priorities")

    def test_route_orion_with_remainder_runs_goal(self) -> None:
        routed = ac._telegram_route_message("orion review my draft", self.profile)
        self.assertEqual(routed.get("action"), "run")
        self.assertEqual(routed.get("goal"), "review my draft")


if __name__ == "__main__":
    unittest.main()
