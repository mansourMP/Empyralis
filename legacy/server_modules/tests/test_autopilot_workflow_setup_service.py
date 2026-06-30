import unittest

from server_modules.connectors.autopilot_workflow_setup_service import AutopilotWorkflowSetupService


class _CameraSetupStub:
    def __init__(self) -> None:
        self.calls = []

    def handle_guided_automation_setup(self, **kwargs):
        self.calls.append(kwargs)
        return {"handled": True, "reply": "ok"}


class AutopilotWorkflowSetupServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotWorkflowSetupService:
        self.camera_setup = overrides.pop("camera_setup", _CameraSetupStub())
        self.requests = overrides.pop("requests", [])
        return AutopilotWorkflowSetupService(
            workflow_api_url="http://workflow.test/api/v1",
            runtime_url="http://runtime.test",
            web_url="http://web.test",
            init_runtime=overrides.pop("init_runtime", lambda: None),
            classify_automation_intent=overrides.pop("classify_automation_intent", lambda text: ""),
            list_vault_connectors=overrides.pop("list_vault_connectors", lambda workspace_id: []),
            http_json_request=overrides.pop(
                "http_json_request",
                lambda *args, **kwargs: self.requests.append({"args": args, "kwargs": kwargs}) or {"json": {}},
            ),
            runtime_api_headers=overrides.pop("runtime_api_headers", lambda: {"X-API-Key": "key"}),
            camera_setup_service=overrides.pop("camera_setup_service", lambda: self.camera_setup),
        )

    def test_workspace_connector_flags_detect_email_and_telegram(self) -> None:
        service = self._make_service(
            list_vault_connectors=lambda workspace_id: [
                {"connector": "telegram_bot"},
                {"connector": "google_workspace"},
            ]
        )
        self.assertEqual(service.workspace_connector_flags("default"), {"telegram": True, "email": True})

    def test_create_email_summary_execution_schedules_posts_weekly_jobs(self) -> None:
        requests = []
        service = self._make_service(
            requests=requests,
            list_vault_connectors=lambda workspace_id: [{"id": "gws-1", "connector": "google_workspace"}],
            http_json_request=lambda *args, **kwargs: requests.append({"args": args, "kwargs": kwargs}) or {"json": {}},
        )

        count = service.create_email_summary_execution_schedules("default", "Support inbox")

        self.assertEqual(count, 7)
        self.assertEqual(len(requests), 7)
        self.assertIn("Email Summary · Support inbox · Monday", requests[0]["kwargs"]["payload"]["name"])

    def test_handle_guided_setup_delegates_to_camera_service_with_internal_callbacks(self) -> None:
        service = self._make_service(
            classify_automation_intent=lambda text: "EMAIL_SUMMARY",
            list_vault_connectors=lambda workspace_id: [{"connector": "telegram_bot"}],
        )

        result = service.handle_telegram_guided_automation_setup(
            workspace_id="default",
            chat_id="chat-1",
            message_text="set up a daily email summary",
            enabled=True,
        )

        self.assertTrue(result["handled"])
        self.assertEqual(len(self.camera_setup.calls), 1)
        call = self.camera_setup.calls[0]
        self.assertEqual(call["workspace_id"], "default")
        self.assertTrue(call["enabled"])
        self.assertEqual(call["workspace_connector_flags"]("default"), {"telegram": True, "email": False})
        self.assertEqual(call["classify_intent"]("x"), "EMAIL_SUMMARY")


if __name__ == "__main__":
    unittest.main()
