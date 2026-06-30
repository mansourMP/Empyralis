import unittest
from pathlib import Path

from server_modules.connectors.telegram_run_action_service import TelegramRunActionService


class _RunDispatchStub:
    def __init__(self) -> None:
        self.calls = []

    def dispatch_run_action(self, **kwargs):
        self.calls.append(kwargs)
        return {"run_id": "run-123"}


class TelegramRunActionServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramRunActionService:
        messages = overrides.pop("messages", [])
        records = overrides.pop("records", [])
        dispatch = overrides.pop("dispatch", _RunDispatchStub())
        return TelegramRunActionService(
            onboarding_enabled=overrides.pop("onboarding_enabled", True),
            space_status_enabled=overrides.pop("space_status_enabled", True),
            max_reply_chars=240,
            project_root=Path("/tmp/repo"),
            onboarding_get_state=overrides.pop("onboarding_get_state", lambda workspace_id, chat_id: {"active": False}),
            onboarding_start=overrides.pop("onboarding_start", lambda workspace_id, chat_id: {"active": True, "step_index": 0}),
            onboarding_consume_answer=overrides.pop(
                "onboarding_consume_answer",
                lambda workspace_id, chat_id, text: {"reply": "next", "completed": False},
            ),
            onboarding_prompt=overrides.pop("onboarding_prompt", lambda step_index, retry: f"prompt-{step_index}"),
            profile_get=overrides.pop("profile_get", lambda workspace_id, chat_id: {"project": "alpha"}),
            profile_has_context=overrides.pop("profile_has_context", lambda chat_profile: bool(chat_profile)),
            help_text=overrides.pop("help_text", lambda profile: "help"),
            build_goal_with_profile=overrides.pop("build_goal_with_profile", lambda goal, chat_profile: f"profile::{goal}"),
            build_goal_with_attachments=overrides.pop("build_goal_with_attachments", lambda goal, attachments: goal),
            workspace_connector_context=overrides.pop("workspace_connector_context", lambda **kwargs: {"prompt_append": "connector"}),
            build_goal_with_connector_context=overrides.pop(
                "build_goal_with_connector_context",
                lambda goal, prompt_append: f"{goal}|{prompt_append}" if prompt_append else goal,
            ),
            space_question_via_mcp=overrides.pop("space_question_via_mcp", lambda goal, enabled, project_root: {}),
            installed_skill_query=overrides.pop("installed_skill_query", lambda **kwargs: {}),
            truncate_one_line=overrides.pop("truncate_one_line", lambda text, limit: str(text)[:limit]),
            send_message=lambda **kwargs: messages.append(kwargs),
            send_chat_action=lambda *args, **kwargs: None,
            edit_message=lambda *args, **kwargs: False,
            record_channel_event=lambda **kwargs: records.append(kwargs),
            run_dispatch_service=lambda: dispatch,
            create_run=lambda **kwargs: {"run_id": "run-123"},
        )

    def test_active_onboarding_consumes_answer(self) -> None:
        messages = []
        service = self._make_service(
            messages=messages,
            onboarding_get_state=lambda workspace_id, chat_id: {"active": True, "step_index": 2},
            onboarding_consume_answer=lambda workspace_id, chat_id, text: {"reply": "answer saved", "completed": False},
        )
        result = service.handle_run_action(
            routed={"goal": "do something"},
            message_text="my answer",
            explicit_run_command=False,
            profile={},
            chat_profile={},
            stored_attachments=[],
            workspace_id="ws",
            connector_id="conn",
            connector_entry={},
            bot_token="token",
            chat_id="chat",
            sender_id="user",
            update_id=1,
            inbound_message_id="msg",
            session_key="session",
            trace_id="trace",
            source_event_id="evt",
        )
        self.assertEqual(result["action"], "onboarding_step")
        self.assertEqual(messages[0]["text"], "answer saved")

    def test_direct_response_short_circuits_run(self) -> None:
        messages = []
        records = []
        dispatch = _RunDispatchStub()
        service = self._make_service(
            messages=messages,
            records=records,
            dispatch=dispatch,
            profile_has_context=lambda chat_profile: True,
            space_question_via_mcp=lambda goal, enabled, project_root: {"handled": True, "response": "space reply", "skill_id": "space"},
        )
        result = service.handle_run_action(
            routed={"goal": "status"},
            message_text="status",
            explicit_run_command=True,
            profile={"id": "profile"},
            chat_profile={"project": "alpha"},
            stored_attachments=[],
            workspace_id="ws",
            connector_id="conn",
            connector_entry={},
            bot_token="token",
            chat_id="chat",
            sender_id="user",
            update_id=1,
            inbound_message_id="msg",
            session_key="session",
            trace_id="trace",
            source_event_id="evt",
        )
        self.assertEqual(result["action"], "skill_reply")
        self.assertEqual(messages[0]["text"], "space reply")
        self.assertEqual(len(records), 1)
        self.assertEqual(dispatch.calls, [])

    def test_dispatches_run_when_no_direct_response(self) -> None:
        dispatch = _RunDispatchStub()
        service = self._make_service(
            dispatch=dispatch,
            profile_has_context=lambda chat_profile: True,
        )
        result = service.handle_run_action(
            routed={"goal": "do work", "skill": {"id": "crm"}},
            message_text="run do work",
            explicit_run_command=True,
            profile={"id": "profile"},
            chat_profile={"project": "alpha"},
            stored_attachments=[],
            workspace_id="ws",
            connector_id="conn",
            connector_entry={"id": "entry"},
            bot_token="token",
            chat_id="chat",
            sender_id="user",
            update_id=1,
            inbound_message_id="msg",
            session_key="session",
            trace_id="trace",
            source_event_id="evt",
        )
        self.assertEqual(result["action"], "run")
        self.assertEqual(result["run_id"], "run-123")
        self.assertEqual(len(dispatch.calls), 1)


if __name__ == "__main__":
    unittest.main()
