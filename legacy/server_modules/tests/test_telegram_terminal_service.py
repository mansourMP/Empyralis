import asyncio
import unittest

from server_modules.connectors.telegram_terminal_service import TelegramTerminalService


class TelegramTerminalServiceTests(unittest.TestCase):
    def _make_service(self, **overrides):
        self.sent = overrides.pop("sent", [])
        self.state_updates = overrides.pop("state_updates", [])
        self.runs = overrides.pop("runs", {})
        return TelegramTerminalService(
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "").strip() or "default"),
            chat_id_from_session_key=overrides.pop("chat_id_from_session_key", lambda key: key.split(":", 1)[1] if key.startswith("telegram:") else ""),
            list_connector_entries=overrides.pop(
                "list_connector_entries",
                lambda: [{"id": "conn-1", "label": "Bot", "workspace_id": "default"}],
            ),
            get_secret=overrides.pop("get_secret", lambda entry: {"bot_token": "bot", "chat_id": "chat-1"}),
            resolve_profile=overrides.pop("resolve_profile", lambda entry: {"id": "assistant"}),
            route_message=overrides.pop("route_message", lambda text, profile: {"action": "run", "goal": "do work"}),
            get_chat_profile=overrides.pop("get_chat_profile", lambda workspace_id, chat_id: {"project": "alpha"}),
            build_goal_with_profile=overrides.pop("build_goal_with_profile", lambda goal, profile: f"profile::{goal}"),
            workspace_connector_context=overrides.pop("workspace_connector_context", lambda **kwargs: {"prompt_append": "connector"}),
            build_goal_with_connector_context=overrides.pop(
                "build_goal_with_connector_context",
                lambda goal, prompt: f"{goal}|{prompt}" if prompt else goal,
            ),
            installed_skill_query=overrides.pop("installed_skill_query", lambda **kwargs: {"handled": False, "prompt_append": ""}),
            create_run=overrides.pop("create_run", lambda **kwargs: {"run_id": "run-1", "route": "cloud"}),
            wait_for_run_terminal_status=overrides.pop(
                "wait_for_run_terminal_status",
                lambda run_id, timeout_seconds=None, max_reply_chars=None: {"status": "completed", "summary": "Done"},
            ),
            runs_get=overrides.pop("runs_get", lambda run_id: self.runs.get(run_id, {"context": {"metadata": {"source": "telegram"}}})),
            session_key=overrides.pop("session_key", lambda chat_id: f"telegram:{chat_id}"),
            safe_path_token=overrides.pop("safe_path_token", lambda value: "safe"),
            send_message=overrides.pop("send_message", lambda **kwargs: self.sent.append(kwargs) or "msg-1"),
            set_connector_state=overrides.pop("set_connector_state", lambda connector_id, patch: self.state_updates.append((connector_id, patch))),
            utc_now_iso=overrides.pop("utc_now_iso", lambda: "2026-04-05T00:00:00Z"),
        )

    def test_handle_send_message_updates_connector_state(self):
        service = self._make_service()
        result = asyncio.run(service.handle_send_message(text="hello", workspace_id="default"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["connector_id"], "conn-1")
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.state_updates[0][0], "conn-1")

    def test_handle_send_message_respects_requested_workspace_across_visible_entries(self):
        service = self._make_service(
            list_connector_entries=lambda: [
                {"id": "conn-default", "label": "Default Bot", "workspace_id": "default"},
                {"id": "conn-ws1", "label": "Workspace Bot", "workspace_id": "ws-1"},
            ],
            get_secret=lambda entry: {
                "bot_token": f"bot-{entry['id']}",
                "chat_id": f"chat-{entry['id']}",
            },
        )
        result = asyncio.run(service.handle_send_message(text="hello", workspace_id="ws-1"))
        self.assertEqual(result["connector_id"], "conn-ws1")
        self.assertEqual(self.sent[0]["bot_token"], "bot-conn-ws1")
        self.assertEqual(self.sent[0]["chat_id"], "chat-conn-ws1")

    def test_handle_send_message_prefers_requested_workspace_when_listing_service_supports_scope(self):
        def _list_connector_entries(workspace_id=None):
            if workspace_id == "ws-1":
                return [{"id": "conn-ws1", "label": "Workspace Bot", "workspace_id": "ws-1"}]
            return [{"id": "conn-default", "label": "Default Bot", "workspace_id": "default"}]

        service = self._make_service(
            list_connector_entries=_list_connector_entries,
            get_secret=lambda entry: {
                "bot_token": f"bot-{entry['id']}",
                "chat_id": f"chat-{entry['id']}",
            },
        )
        result = asyncio.run(service.handle_send_message(text="hello", workspace_id="ws-1"))
        self.assertEqual(result["connector_id"], "conn-ws1")
        self.assertEqual(self.sent[0]["bot_token"], "bot-conn-ws1")

    def test_autopilot_test_short_circuits_direct_response(self):
        service = self._make_service(
            installed_skill_query=lambda **kwargs: {"handled": True, "response": "direct", "prompt_append": ""},
        )
        result = asyncio.run(service.handle_autopilot_test_message(text="help", workspace_id="default"))
        self.assertEqual(result["mode"], "autopilot_test")
        self.assertEqual(result["result"]["reply"], "direct")

    def test_autopilot_test_creates_run_when_needed(self):
        runs = {"run-1": {"context": {"metadata": {"k": "v"}}}}
        service = self._make_service(runs=runs)
        result = asyncio.run(service.handle_autopilot_test_message(text="do work", workspace_id="default"))
        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["result"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
