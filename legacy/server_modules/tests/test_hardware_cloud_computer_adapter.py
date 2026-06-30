from __future__ import annotations

import unittest

from server_modules import virtual_computer_runtime
from server_modules.hardware_runtime_adapters import cloud_computer_adapter


class HardwareCloudComputerAdapterTests(unittest.TestCase):
    def test_virtual_action_mapping_covers_browser_shell_and_screenshot(self) -> None:
        action, args, dispatch = cloud_computer_adapter.cloud_computer_virtual_action(
            capability_id="browser_automation.interactive",
            action_id="browser.open",
            arguments={"url": "https://example.com"},
        )
        self.assertEqual(action, virtual_computer_runtime.ACTION_OPEN_URL)
        self.assertEqual(args["url"], "https://example.com")
        self.assertEqual(dispatch, "execute_action")

        action, args, dispatch = cloud_computer_adapter.cloud_computer_virtual_action(
            capability_id="shell.execute",
            action_id="shell.exec",
            arguments={"command": "echo hi"},
        )
        self.assertEqual(action, virtual_computer_runtime.ACTION_RUN_COMMAND)
        self.assertEqual(args["command"], "echo hi")
        self.assertEqual(dispatch, "execute_action")

        action, args, dispatch = cloud_computer_adapter.cloud_computer_virtual_action(
            capability_id="screenshot.capture",
            action_id="screen",
            arguments={},
        )
        self.assertEqual(action, virtual_computer_runtime.ACTION_SCREENSHOT)
        self.assertEqual(args, {})
        self.assertEqual(dispatch, "stream_screenshot")

    def test_full_access_skips_cloud_computer_action_approval(self) -> None:
        self.assertFalse(
            cloud_computer_adapter.cloud_computer_approval_required(
                capability_id="shell.execute",
                action_id="shell.exec",
                virtual_action=virtual_computer_runtime.ACTION_RUN_COMMAND,
                arguments={"command": "rm -rf /tmp/demo"},
                runtime_access_mode="full_access",
                require_approval=None,
            )
        )
        self.assertTrue(
            cloud_computer_adapter.cloud_computer_approval_required(
                capability_id="shell.execute",
                action_id="shell.exec",
                virtual_action=virtual_computer_runtime.ACTION_RUN_COMMAND,
                arguments={"command": "rm -rf /tmp/demo"},
                runtime_access_mode="default_guarded",
                require_approval=None,
            )
        )

    def test_runtime_cost_metadata_and_session_defaults_are_stable(self) -> None:
        metadata = cloud_computer_adapter.runtime_cost_metadata(
            {
                "runtime_provider_id": "provider-1",
                "runtime_kind": "virtual",
                "cost_usage": {"credits": 2},
                "action_result": {"cost_estimate": {"credits": 1}},
            },
            runtime_choice="virtual_browser",
            provider_id=None,
        )
        self.assertEqual(metadata["runtime_choice"], "virtual_browser")
        self.assertEqual(metadata["runtime_provider_id"], "provider-1")
        self.assertEqual(metadata["cost_usage"], {"credits": 2})
        self.assertEqual(metadata["action_cost_estimate"], {"credits": 1})

        persistence = cloud_computer_adapter.cloud_computer_session_persistence(None)
        self.assertEqual(persistence["session_mode"], virtual_computer_runtime.SESSION_PERSISTENCE_EPHEMERAL)
        self.assertTrue(persistence["auto_terminate_on_task_complete"])
