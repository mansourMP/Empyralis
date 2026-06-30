from __future__ import annotations

import unittest

from server_modules import agent_computer_surface_service as surface


class AgentComputerSurfaceServiceTests(unittest.TestCase):
    def test_activity_shape_uses_simple_user_labels(self) -> None:
        cases = {
            "connecting": ("connecting_hardware", "Connecting to your Mac...", "active"),
            "running": ("hardware_running", "Running on your Mac", "active"),
            "completed": ("done", "Done", "completed"),
            "waiting_approval": ("waiting_approval", "Sage needs your approval", "active"),
            "offline": ("error", "Agent Computer offline", "failed"),
            "requires_agent_computer": ("error", "Needs Agent Computer", "failed"),
        }
        for state, (activity_type, label, status) in cases.items():
            with self.subTest(state=state):
                shape = surface.agent_computer_activity_shape(state)
                self.assertEqual(shape["type"], activity_type)
                self.assertEqual(shape["label"], label)
                self.assertEqual(shape["status"], status)

    def test_public_status_labels_include_required_and_connected_states(self) -> None:
        self.assertEqual(surface.agent_computer_public_status_label("online"), "Connected")
        self.assertEqual(surface.agent_computer_public_status_label("running"), "Running on your Mac")
        self.assertEqual(surface.agent_computer_public_status_label("requires_agent_computer"), "Needs Agent Computer")
        self.assertEqual(surface.agent_computer_public_status_label("offline"), "Agent Computer offline")


if __name__ == "__main__":
    unittest.main()
