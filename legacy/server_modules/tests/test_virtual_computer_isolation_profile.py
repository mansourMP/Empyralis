import asyncio
import unittest

from server_modules.virtual_computer_runtime import (
    InMemoryVirtualComputerRuntime,
    build_isolation_profile,
)


class VirtualComputerIsolationProfileTests(unittest.TestCase):
    def test_default_profile_locks_down_file_and_clipboard(self):
        profile = build_isolation_profile({}, runtime_choice="virtual_browser")

        self.assertFalse(profile.clipboard_enabled)
        self.assertFalse(profile.file_transfer_enabled)
        self.assertTrue(profile.block_metadata_endpoints)
        self.assertFalse(profile.allow_private_lan)

    def test_private_lan_and_metadata_hosts_are_blocked(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "navigate",
                        "action_args": {"url": "http://127.0.0.1:8000"},
                    }
                )
            )

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "navigate",
                        "action_args": {"url": "http://169.254.169.254/latest/meta-data"},
                    }
                )
            )

    def test_file_transfer_is_blocked_by_default(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "download_file",
                        "action_args": {"url": "https://example.com/file.txt"},
                    }
                )
            )

    def test_kill_switch_terminates_session(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(runtime.create_session({"runtime_choice": "virtual_browser"}))
        session_id = created.get("session_id")

        kill = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "kill_switch",
                    "action_args": {},
                }
            )
        )
        self.assertEqual(kill.get("status"), "terminated")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "navigate",
                        "action_args": {"url": "https://example.com"},
                    }
                )
            )

    def test_ttl_auto_destroy_blocks_followup_actions(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "isolation_profile": {"runtime_ttl_seconds": 60},
                }
            )
        )
        session_id = created.get("session_id")
        runtime._isolation_by_session[session_id].expires_at_epoch = 0.0

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "navigate",
                        "action_args": {"url": "https://example.com"},
                    }
                )
            )

    def test_cost_quota_blocks_over_budget_session(self):
        runtime = InMemoryVirtualComputerRuntime()

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.create_session(
                    {
                        "runtime_choice": "virtual_browser",
                        "workspace_id": "workspace_cost_limited",
                        "cost_quota": {
                            "workspace_budget_limit": 0.5,
                            "estimated_create_cost": 1.0,
                        },
                    }
                )
            )

    def test_provider_concurrency_quota_blocks_second_session(self):
        runtime = InMemoryVirtualComputerRuntime()
        payload = {
            "runtime_choice": "virtual_browser",
            "runtime_provider_id": "browserbase",
            "cost_quota": {"provider_concurrency_limit": 1},
        }

        asyncio.run(runtime.create_session(payload))

        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.create_session(payload))

    def test_per_session_cost_limit_kills_runaway_session(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "cost_quota": {
                        "per_session_cost_limit": 1.5,
                        "estimated_create_cost": 1.0,
                        "estimated_action_cost": 1.0,
                    },
                }
            )
        )

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": created.get("session_id"),
                        "action": "navigate",
                        "action_args": {"url": "https://example.com"},
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
