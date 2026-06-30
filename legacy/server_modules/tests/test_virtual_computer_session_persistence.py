import asyncio
import time
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerSessionPersistenceTests(unittest.TestCase):
    def test_resumable_session_supports_pause_resume(self):
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
        self.assertEqual((created.get("session_persistence") or {}).get("session_mode"), "resumable")

        paused = asyncio.run(runtime.pause_session({"session_id": session_id}))
        self.assertEqual(paused.get("state"), "paused")
        resumed = asyncio.run(runtime.resume_session({"session_id": session_id}))
        self.assertEqual(resumed.get("state"), "running")

    def test_ephemeral_session_auto_terminates_after_action(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_code_sandbox",
                    "session_persistence": {"session_mode": "ephemeral"},
                }
            )
        )
        session_id = created.get("session_id")

        result = asyncio.run(
            runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "run_command",
                        "action_args": {"command": "echo hello"},
                        "approved": True,
                        "risk_policy": {"red_policy": "owner_approval"},
                        "policy_metadata": {"owner_role": "owner", "owner_is_admin": True},
                    }
                )
            )
        self.assertEqual(result.get("state"), "terminated")
        self.assertTrue(result.get("auto_terminated"))

    def test_ephemeral_session_cannot_pause(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "session_persistence": {"session_mode": "ephemeral"},
                }
            )
        )
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.pause_session({"session_id": session_id}))

    def test_collect_artifact_enforces_ttl_retention(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "session_persistence": {"screenshot_ttl_seconds": 60},
                }
            )
        )
        session_id = created.get("session_id")
        streamed = asyncio.run(runtime.stream_screenshot({"session_id": session_id}))
        artifact_id = (streamed.get("artifact") or {}).get("artifact_id")

        session = runtime._sessions[session_id]
        session["artifacts"][0]["expires_at_epoch"] = time.time() - 1

        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.collect_artifact({"session_id": session_id, "artifact_id": artifact_id}))

    def test_manual_terminate_policy_can_block_manual_terminate(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "session_persistence": {"manual_terminate_enabled": False},
                }
            )
        )
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.terminate_session({"session_id": session_id, "manual_terminate": True}))


if __name__ == "__main__":
    unittest.main()
