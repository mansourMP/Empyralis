import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerEphemeralSessionTests(unittest.TestCase):
    def test_ephemeral_screenshot_auto_terminates_and_releases_quota(self):
        runtime = InMemoryVirtualComputerRuntime()
        payload = {
            "runtime_choice": "virtual_browser",
            "runtime_quota": {"provider_concurrency_limit": 1},
            "ephemeral_task": True,
            "session_persistence": {
                "session_mode": "ephemeral",
                "auto_terminate_on_task_complete": True,
            },
        }

        created = asyncio.run(runtime.create_session(payload))
        streamed = asyncio.run(runtime.stream_screenshot({"session_id": created.get("session_id")}))

        self.assertEqual(streamed.get("state"), "terminated")
        self.assertEqual(streamed.get("status"), "terminated")
        self.assertTrue(streamed.get("auto_terminated"))
        self.assertTrue((streamed.get("artifact") or {}).get("artifact_id"))

        second = asyncio.run(runtime.create_session(payload))
        self.assertEqual(second.get("state"), "running")


if __name__ == "__main__":
    unittest.main()
