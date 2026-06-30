import json
import tempfile
import unittest
from pathlib import Path

from server_modules.connectors.telegram_camera_setup_service import TelegramCameraSetupService


class TelegramCameraSetupServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory(prefix="telegram-camera-setup-")
        self.addCleanup(self._tmpdir.cleanup)
        root = Path(self._tmpdir.name)
        self.state_path = root / "camera_setup.json"
        self.service = TelegramCameraSetupService(
            state_file=self.state_path,
            read_json=self._read_json,
            write_json=self._write_json,
            now_iso=lambda: "2026-04-04T00:00:00Z",
            session_key_builder=lambda workspace_id, chat_id: f"{workspace_id}:{chat_id}",
        )

    def _read_json(self, path: Path, default: dict) -> dict:
        if not path.exists():
            return dict(default)
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_camera_setup_state_round_trip_and_clear(self) -> None:
        state = self.service.set_state("default", "123", "awaiting_summary_target", "summarize email", intent="EMAIL_SUMMARY")
        self.assertTrue(state["active"])
        self.assertEqual(state["intent"], "EMAIL_SUMMARY")

        loaded = self.service.get_state("default", "123")
        self.assertEqual(loaded["stage"], "awaiting_summary_target")

        self.service.clear_state("default", "123")
        cleared = self.service.get_state("default", "123")
        self.assertFalse(cleared["active"])

    def test_guided_setup_starts_email_summary_flow(self) -> None:
        result = self.service.handle_guided_automation_setup(
            workspace_id="default",
            chat_id="123",
            message_text="set up a daily email summary",
            enabled=True,
            classify_intent=lambda text: "EMAIL_SUMMARY",
            workspace_connector_flags=lambda workspace_id: {"telegram": True, "email": True},
            create_email_summary_visibility_record=lambda target_label, telegram_connected: "wf-1",
            create_email_summary_execution_schedules=lambda workspace_id, target_label: 7,
            email_summary_completion_text=lambda *args, **kwargs: "done",
            create_lead_followup_visibility_record=lambda *args, **kwargs: "wf-2",
            create_lead_followup_execution_schedules=lambda *args, **kwargs: 7,
            lead_followup_completion_text=lambda *args, **kwargs: "done",
        )

        self.assertTrue(result["handled"])
        self.assertIn("Which inbox should I summarize?", result["reply"])
        state = self.service.get_state("default", "123")
        self.assertEqual(state["stage"], "awaiting_summary_target")

    def test_guided_setup_completes_followup_flow(self) -> None:
        self.service.set_state("default", "123", "awaiting_followup_target", "follow up leads", intent="LEAD_FOLLOWUP")

        result = self.service.handle_guided_automation_setup(
            workspace_id="default",
            chat_id="123",
            message_text="Website leads",
            enabled=True,
            classify_intent=lambda text: "",
            workspace_connector_flags=lambda workspace_id: {"telegram": True, "email": True},
            create_email_summary_visibility_record=lambda target_label, telegram_connected: "wf-1",
            create_email_summary_execution_schedules=lambda workspace_id, target_label: 7,
            email_summary_completion_text=lambda *args, **kwargs: "done",
            create_lead_followup_visibility_record=lambda target_label, email_connected, telegram_connected: "wf-2",
            create_lead_followup_execution_schedules=lambda workspace_id, target_label: 5,
            lead_followup_completion_text=lambda label, schedule_count, email_connected, workflow_id: f"{label}:{schedule_count}:{workflow_id}",
        )

        self.assertTrue(result["handled"])
        self.assertIn("Website leads:5:wf-2", result["reply"])
        self.assertFalse(self.service.get_state("default", "123")["active"])
