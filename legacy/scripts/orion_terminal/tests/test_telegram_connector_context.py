import importlib.util
import unittest
from pathlib import Path


try:
    module_path = Path(__file__).resolve().parents[3] / "server_modules" / "autopilot_connectors.py"
    spec = importlib.util.spec_from_file_location("orion_test_autopilot_connectors_connectors", module_path)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"cannot load module spec: {module_path}")
    ac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ac)
    _IMPORT_ERROR = None
except ModuleNotFoundError as exc:
    ac = None
    _IMPORT_ERROR = exc


class TelegramConnectorContextTests(unittest.TestCase):
    def setUp(self) -> None:
        if ac is None:
            self.skipTest(f"server_modules dependencies unavailable: {_IMPORT_ERROR}")
        self._orig_server = getattr(ac, "_server", None)
        self._orig_list = getattr(ac, "list_vault_connectors", None)
        self._orig_resolve = getattr(ac, "resolve_vault_credential", None)
        self._orig_recent = getattr(ac, "list_recent_connector_messages", None)
        ac._server = object()

    def tearDown(self) -> None:
        if ac is None:
            return
        ac._server = self._orig_server
        if self._orig_list is not None:
            ac.list_vault_connectors = self._orig_list
        if self._orig_resolve is not None:
            ac.resolve_vault_credential = self._orig_resolve
        if self._orig_recent is not None:
            ac.list_recent_connector_messages = self._orig_recent

    def test_email_goal_selects_google_workspace_connector(self) -> None:
        ac.list_vault_connectors = lambda workspace_id=None: [
            {"id": "tg-1", "label": "Telegram", "connector": "telegram_bot", "workspace_id": "default", "metadata": {}},
            {"id": "gws-1", "label": "Google Workspace", "connector": "google_workspace", "workspace_id": "default", "metadata": {}},
        ]
        ac.resolve_vault_credential = lambda credential_id, workspace_id=None: {
            "_provider": "google_workspace",
            "auth_mode": "gws_local",
        }
        ac.list_recent_connector_messages = lambda credentials, limit=3: [
            {
                "id": "msg-1",
                "from": "alice@example.com",
                "subject": "Launch checklist",
                "date": "Thu, 19 Mar 2026 10:00:00 +0000",
                "snippet": "Please review the launch checklist and blockers.",
            }
        ]

        payload = ac._telegram_workspace_connector_context(
            "read my last 3 emails and summarize them",
            "default",
            "tg-1",
        )

        self.assertEqual(payload.get("connector_credential_id"), "gws-1")
        self.assertEqual(payload.get("connector_provider"), "google_workspace")
        self.assertEqual(len(payload.get("available_connectors") or []), 2)
        prompt = str(payload.get("prompt_append") or "")
        self.assertIn("Workspace connectors available", prompt)
        self.assertIn("Recent emails fetched", prompt)
        self.assertIn("Launch checklist", prompt)

    def test_general_goal_keeps_connector_prompt_empty(self) -> None:
        ac.list_vault_connectors = lambda workspace_id=None: [
            {"id": "tg-1", "label": "Telegram", "connector": "telegram_bot", "workspace_id": "default", "metadata": {}},
            {"id": "gws-1", "label": "Google Workspace", "connector": "google_workspace", "workspace_id": "default", "metadata": {}},
        ]
        ac.resolve_vault_credential = lambda credential_id, workspace_id=None: {
            "_provider": "google_workspace",
            "auth_mode": "gws_local",
        }
        ac.list_recent_connector_messages = lambda credentials, limit=3: []

        payload = ac._telegram_workspace_connector_context(
            "help me plan my day",
            "default",
            "tg-1",
        )

        self.assertEqual(payload.get("connector_credential_id"), None)
        self.assertEqual(str(payload.get("prompt_append") or ""), "")
        self.assertEqual(len(payload.get("channel_connectors") or []), 2)


if __name__ == "__main__":
    unittest.main()
