import unittest

from server_modules.connectors.telegram_connector_context_service import TelegramConnectorContextService


class TelegramConnectorContextServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramConnectorContextService:
        return TelegramConnectorContextService(
            installed_skills_enabled=overrides.pop("installed_skills_enabled", True),
            init_runtime=overrides.pop("init_runtime", lambda: None),
            list_vault_connectors=overrides.pop("list_vault_connectors", lambda workspace_id: []),
            resolve_vault_credential=overrides.pop("resolve_vault_credential", lambda credential_id, workspace_id: {}),
            list_recent_connector_messages=overrides.pop("list_recent_connector_messages", lambda credentials, limit: []),
            query_active_installed_skills=overrides.pop("query_active_installed_skills", lambda **kwargs: {"handled": False}),
        )

    def test_workspace_connector_context_loads_recent_email_preview(self) -> None:
        service = self._make_service(
            list_vault_connectors=lambda workspace_id: [
                {"id": "tg-1", "label": "Telegram", "connector": "telegram_bot"},
                {"id": "gws-1", "label": "Google Workspace", "connector": "google_workspace"},
            ],
            resolve_vault_credential=lambda credential_id, workspace_id: {"_provider": "google_workspace"},
            list_recent_connector_messages=lambda credentials, limit: [
                {
                    "from": "alice@example.com",
                    "subject": "Launch checklist",
                    "date": "Thu, 19 Mar 2026 10:00:00 +0000",
                    "snippet": "Please review the launch checklist and blockers.",
                }
            ],
        )

        payload = service.workspace_connector_context(
            "read my last 3 emails and summarize them",
            "default",
            "tg-1",
        )

        self.assertEqual(payload["connector_credential_id"], "gws-1")
        self.assertEqual(payload["connector_provider"], "google_workspace")
        self.assertIn("Workspace connectors available", payload["prompt_append"])
        self.assertIn("Launch checklist", payload["prompt_append"])

    def test_build_goal_with_connector_context_keeps_empty_prompt_clean(self) -> None:
        service = self._make_service()
        self.assertEqual(service.build_goal_with_connector_context("goal", ""), "goal")
        self.assertEqual(service.build_goal_with_connector_context("", "context"), "context")

    def test_installed_skill_query_returns_fallback_error_payload(self) -> None:
        service = self._make_service(
            query_active_installed_skills=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        payload = service.installed_skill_query(
            goal="help",
            workspace_id="ws",
            connector_id="conn",
            chat_id="chat",
            session_key="session",
        )

        self.assertFalse(payload["handled"])
        self.assertEqual(payload["errors"][0]["skill_id"], "installed_skills")


if __name__ == "__main__":
    unittest.main()
