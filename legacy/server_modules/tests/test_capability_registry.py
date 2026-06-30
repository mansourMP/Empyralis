import unittest

from server_modules.capability_registry import (
    canonical_capability_id,
    resolve_capability,
    workflow_node_capability_id,
    workflow_tool_capability_id,
)
from server_modules import safe_mode_service


class CapabilityRegistryTests(unittest.TestCase):
    def tearDown(self) -> None:
        safe_mode_service.reset_state_for_tests()

    def test_resolve_capability_returns_contract(self) -> None:
        contract = resolve_capability("screenshot.capture")

        assert contract is not None
        self.assertEqual(contract.capability_id, "screenshot.capture")
        self.assertEqual(contract.display_name, "Capture Screenshot")
        self.assertIn("local_companion", contract.allowed_environments)
        self.assertIn("cloud_computer", contract.allowed_environments)
        self.assertEqual(contract.artifact_outputs, ["image/png"])

    def test_resolve_capability_returns_none_for_missing_capability(self) -> None:
        self.assertIsNone(resolve_capability("missing.capability"))

    def test_resolve_capability_exposes_risk_level(self) -> None:
        contract = resolve_capability("shell.execute")

        assert contract is not None
        self.assertEqual(contract.risk_level, "critical")
        self.assertTrue(contract.requires_approval)

    def test_resolve_capability_returns_move_mouse_contract(self) -> None:
        contract = resolve_capability("computer_control.move")

        assert contract is not None
        self.assertEqual(contract.capability_id, "computer_control.move")
        self.assertEqual(contract.display_name, "Move Mouse")
        self.assertEqual(contract.required_os_permissions, ["accessibility"])

    def test_resolve_capability_denies_when_global_safe_mode_blocks_unsafe_capability(self) -> None:
        safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertIsNone(resolve_capability("computer_control.click"))
        self.assertIsNone(resolve_capability("browser_automation.interactive"))
        self.assertIsNone(resolve_capability("shell.execute"))
        self.assertIsNotNone(resolve_capability("screenshot.capture"))

    def test_resolve_capability_denies_workspace_machine_and_capability_scoped_switches(self) -> None:
        safe_mode_service.set_kill_switch(scope="workspace", enabled=True, workspace_id="ws-1", reason="workspace freeze")
        safe_mode_service.set_kill_switch(scope="machine", enabled=True, machine_id="machine-1", reason="machine freeze")
        safe_mode_service.set_kill_switch(scope="capability", enabled=True, capability_id="screenshot.capture", reason="cap freeze")

        self.assertIsNone(resolve_capability("filesystem.read_write", workspace_id="ws-1"))
        self.assertIsNone(resolve_capability("filesystem.read_write", machine_id="machine-1"))
        self.assertIsNone(resolve_capability("screenshot.capture"))
        self.assertIsNotNone(resolve_capability("filesystem.read_write", workspace_id="ws-2"))

    def test_canonical_capability_id_resolves_alias_even_if_switch_blocks_execution(self) -> None:
        safe_mode_service.set_safe_mode(enabled=True, reason="incident")

        self.assertEqual(canonical_capability_id("browser_automation"), "browser_automation.interactive")
        self.assertEqual(canonical_capability_id("execute_shell_command"), "shell.execute")

    def test_workflow_node_capability_id_maps_core_node_families(self) -> None:
        self.assertEqual(
            workflow_node_capability_id("trigger", variant="manual"),
            "workflow.trigger.manual",
        )
        self.assertEqual(
            workflow_node_capability_id("decision", variant="classifier"),
            "workflow.decision.classifier",
        )
        self.assertEqual(
            workflow_node_capability_id("subflow", variant="call_workflow"),
            "workflow.subflow.call_workflow",
        )
        self.assertEqual(
            workflow_node_capability_id("loop", variant="repeat"),
            "workflow.loop.repeat",
        )

    def test_workflow_tool_capability_id_maps_connector_and_local_tool_variants(self) -> None:
        self.assertEqual(
            workflow_tool_capability_id(
                "connector_action",
                config={"connector": "telegram_bot", "action_id": "send_message"},
            ),
            "send_message",
        )
        self.assertEqual(
            workflow_tool_capability_id(
                "connector_action",
                config={"connector": "github", "action_id": "list_issues"},
            ),
            "connector.action.read",
        )
        self.assertEqual(
            workflow_tool_capability_id("browser", config={}),
            "browser_automation.interactive",
        )
        self.assertEqual(
            workflow_tool_capability_id("code", config={}),
            "code.execute_reviewed",
        )


if __name__ == "__main__":
    unittest.main()
