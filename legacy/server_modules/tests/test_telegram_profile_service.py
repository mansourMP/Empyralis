import json
import tempfile
import unittest
from pathlib import Path

from server_modules.connectors.telegram_profile_service import TelegramProfileService


class TelegramProfileServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="telegram-profile-service-")
        self.addCleanup(self._tmpdir.cleanup)
        root = Path(self._tmpdir.name)
        self.profile_path = root / "profiles.json"
        self.onboarding_path = root / "onboarding.json"
        self.service = TelegramProfileService(
            profile_state_file=self.profile_path,
            onboarding_state_file=self.onboarding_path,
            default_chat_prefix="/empyralis",
            read_json=self._read_json,
            write_json=self._write_json,
            now_iso=lambda: "2026-04-04T00:00:00Z",
            truncate_one_line=lambda text, limit: str(text)[:limit],
        )

    def _read_json(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return dict(default)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_profile_round_trip_and_clear(self) -> None:
        profile = self.service.set_profile_field("default", "123", "project", "Launching MVP soon")
        self.assertEqual(profile["project"], "Launching MVP soon")

        loaded = self.service.get_profile("default", "123")
        self.assertEqual(loaded["project"], "Launching MVP soon")

        cleared = self.service.clear_profile("default", "123", "project")
        self.assertEqual(cleared["project"], "")

    def test_build_goal_with_profile_embeds_saved_context(self) -> None:
        merged = self.service.build_goal_with_profile(
            "What should I do next?",
            {"project": "launching MVP", "exam": "math finals next week"},
        )

        self.assertIn("User context", merged)
        self.assertIn("launching MVP", merged)
        self.assertIn("User request", merged)

    def test_normalize_profile_field_supports_aliases(self) -> None:
        self.assertEqual(self.service.normalize_profile_field("prefs"), "preferences")
        self.assertEqual(self.service.normalize_profile_field("/work"), "project")
        self.assertEqual(self.service.normalize_profile_field(""), "")

    def test_onboarding_consume_answer_retries_low_quality_and_advances(self) -> None:
        start = self.service.start_onboarding("default", "123")
        self.assertTrue(start["active"])

        retry = self.service.onboarding_consume_answer("default", "123", "idk")
        self.assertFalse(retry["completed"])
        self.assertIn("bit more detail", retry["reply"])

        next_step = self.service.onboarding_consume_answer("default", "123", "I am Mansur building an AI platform.")
        self.assertFalse(next_step["completed"])
        self.assertIn("2/3", next_step["reply"])

    def test_profile_text_and_help_use_prefix_mode(self) -> None:
        profile_text = self.service.profile_text(
            {"prefix": "/orion", "require_prefix": True},
            {"project": "Launch"},
        )
        help_text = self.service.profile_help_text({"prefix": "/orion", "require_prefix": True})

        self.assertIn("`/orion me`", profile_text)
        self.assertIn("/orion me set project", help_text)

    def test_profile_text_and_help_include_privacy_commands_for_deployed_agent_profiles(self) -> None:
        profile_text = self.service.profile_text(
            {"prefix": "/orion", "require_prefix": True, "deployed_agent_id": "dagent-1"},
            {"project": "Launch"},
        )
        help_text = self.service.profile_help_text(
            {"prefix": "/orion", "require_prefix": True, "deployed_agent_id": "dagent-1"},
        )

        self.assertIn("`/orion privacy`", profile_text)
        self.assertIn("/orion delete", help_text)
