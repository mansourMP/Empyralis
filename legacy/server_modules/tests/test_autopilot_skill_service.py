import unittest

from server_modules.connectors.autopilot_skill_service import AutopilotSkillService


class AutopilotSkillServiceTests(unittest.TestCase):
    def _make_service(self, *, snapshot=None, builtin=None) -> AutopilotSkillService:
        return AutopilotSkillService(
            default_chat_prefix="/empyralis",
            init_runtime=lambda: None,
            runtime_builtin_skills_getter=lambda: builtin
            if builtin is not None
            else [
                {"id": "crm", "title": "CRM", "intent": "Handle CRM work", "tools": ["shell__exec"]},
                {"id": "ops", "title": "Operations", "intent": "Handle ops work"},
            ],
            runtime_skills_snapshot_getter=lambda: (lambda: snapshot)
            if snapshot is not None
            else (
                lambda: {
                    "bindings": {"assistant_defaults": ["crm"]},
                    "custom_skills": [
                        {"id": "brief", "title": "Brief", "intent": "Create a brief"},
                    ],
                }
            ),
        )

    def test_runtime_active_skills_prefers_bound_ids(self) -> None:
        service = self._make_service()
        active = service.runtime_active_skills("assistant_defaults", limit=8)
        self.assertEqual([item["id"] for item in active], ["crm"])

    def test_select_skill_from_text_matches_id_and_partial_title(self) -> None:
        service = self._make_service(
            snapshot={"bindings": {}, "custom_skills": []},
        )
        self.assertEqual(service.select_skill_from_text("skill crm")["id"], "crm")
        self.assertEqual(service.select_skill_from_text("ope")["id"], "ops")

    def test_telegram_skill_goal_and_menu_text(self) -> None:
        service = self._make_service(
            snapshot={"bindings": {}, "custom_skills": []},
        )
        goal = service.telegram_skill_goal({"id": "crm", "title": "CRM", "intent": "Handle CRM work", "tools": ["shell__exec"]})
        self.assertIn("Apply skill 'CRM'", goal)
        self.assertIn("shell__exec", goal)

        text = service.telegram_skills_menu_text({"require_prefix": True, "prefix": "/ops"})
        self.assertIn("/ops skill crm", text)
        self.assertIn("/ops menu", text)


if __name__ == "__main__":
    unittest.main()
