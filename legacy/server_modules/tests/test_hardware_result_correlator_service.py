from __future__ import annotations

import unittest

from server_modules import hardware_result_correlator_service as correlator


class HardwareResultCorrelatorServiceTests(unittest.TestCase):
    def test_hardware_agent_activity_uses_simple_agent_computer_labels(self) -> None:
        self.assertEqual(
            correlator._hardware_agent_activity(tool_call_id="tool-1", state="running")["label"],
            "Running on your Mac",
        )
        self.assertEqual(
            correlator._hardware_agent_activity(tool_call_id="tool-2", state="completed")["label"],
            "Done",
        )
        self.assertEqual(
            correlator._hardware_agent_activity(tool_call_id="tool-3", state="offline")["label"],
            "Agent Computer offline",
        )
        self.assertEqual(
            correlator._hardware_agent_activity(tool_call_id="tool-4", state="requires_agent_computer")["label"],
            "Needs Agent Computer",
        )

    def test_hardware_agent_activity_keeps_internal_markup_out_of_details(self) -> None:
        activity = correlator._hardware_agent_activity(
            tool_call_id="tool-1",
            state="running",
            summary="<|| DSML || tool_calls><|| DSML || invoke name='bash'>",
        )
        self.assertNotIn("detail", activity)

    def test_artifact_ids_from_execution_collects_result_artifacts(self) -> None:
        self.assertEqual(
            correlator.artifact_ids_from_execution(
                {
                    "result": {
                        "artifact_id": "artifact-1",
                        "artifacts": [{"id": "artifact-2"}, "artifact-3"],
                    }
                }
            ),
            ["artifact-1", "artifact-2", "artifact-3"],
        )

    def test_runtime_artifact_ids_from_response_collects_streaming_screenshot(self) -> None:
        self.assertEqual(
            correlator.runtime_artifact_ids_from_response(
                {
                    "artifact": {"id": "artifact-1"},
                    "streaming_ui": {
                        "live_screenshot": {"artifact_id": "artifact-2"},
                        "artifacts": [{"id": "artifact-3"}],
                    },
                }
            ),
            ["artifact-1", "artifact-2", "artifact-3"],
        )


if __name__ == "__main__":
    unittest.main()
