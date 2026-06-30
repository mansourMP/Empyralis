import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerStreamingUiTests(unittest.TestCase):
    def test_streaming_ui_includes_live_stream_timeline_context_controls_and_artifacts(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "session_persistence": {"session_mode": "resumable"},
                }
            )
        )
        session_id = created.get("session_id")

        asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "open_url",
                    "action_args": {"url": "https://example.com", "app_title": "Example"},
                }
            )
        )
        streamed = asyncio.run(runtime.stream_screenshot({"session_id": session_id}))
        ui = streamed.get("streaming_ui") or {}

        self.assertEqual((ui.get("current_context") or {}).get("url"), "https://example.com")
        self.assertEqual((ui.get("current_context") or {}).get("app_title"), "Example")
        self.assertTrue(bool(ui.get("live_screenshot")))
        self.assertTrue(bool(ui.get("artifact_drawer")))
        self.assertTrue(any((item or {}).get("event_type") == "stream" for item in (ui.get("action_timeline") or [])))
        controls = ui.get("controls") or {}
        self.assertTrue(controls.get("pause"))
        self.assertTrue(controls.get("resume"))
        self.assertTrue(controls.get("stop"))
        self.assertTrue(controls.get("take_over"))

    def test_risk_prompt_and_approval_why_card_are_inlined(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        result = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "type",
                    "approval_id": "appr_vc10",
                    "action_args": {"text": "hello", "target_description": "login form submit field"},
                }
            )
        )
        approval_prompt = (result.get("action_result") or {}).get("approval_prompt") or {}
        inline_prompt = (result.get("streaming_ui") or {}).get("risk_prompt") or {}

        self.assertEqual(approval_prompt.get("risk_class"), "ORANGE")
        self.assertTrue(approval_prompt.get("requires_approval"))
        self.assertTrue(bool(approval_prompt.get("why_approval_is_needed")))
        self.assertEqual(inline_prompt.get("risk_class"), "ORANGE")

    def test_ephemeral_streaming_ui_disables_pause_and_resume(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "session_persistence": {"session_mode": "ephemeral"},
                }
            )
        )
        controls = ((created.get("streaming_ui") or {}).get("controls") or {})
        self.assertFalse(controls.get("pause"))
        self.assertFalse(controls.get("resume"))
        self.assertTrue(controls.get("stop"))


if __name__ == "__main__":
    unittest.main()
