import asyncio
import unittest

from server_modules.virtual_computer_runtime import InMemoryVirtualComputerRuntime


class VirtualComputerCostQuotaManagerTests(unittest.TestCase):
    def test_workspace_monthly_budget_blocks_new_session(self):
        runtime = InMemoryVirtualComputerRuntime()
        asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "workspace_id": "ws_a",
                    "cost_quota": {
                        "estimated_create_cost": 6.0,
                        "workspace_monthly_budget_limit": 10.0,
                    },
                }
            )
        )
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.create_session(
                    {
                        "runtime_choice": "virtual_browser",
                        "workspace_id": "ws_a",
                        "cost_quota": {
                            "estimated_create_cost": 6.0,
                            "workspace_monthly_budget_limit": 10.0,
                        },
                    }
                )
            )

    def test_long_task_cost_estimate_requires_approval(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_code_sandbox",
                    "cost_quota": {
                        "estimated_action_cost": 5.0,
                        "long_task_cost_estimate_threshold": 6.0,
                    },
                }
            )
        )
        session_id = created.get("session_id")
        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "run_command",
                        "risk_policy": {"red_policy": "owner_approval"},
                        "policy_metadata": {"owner_role": "owner", "owner_is_admin": True},
                        "approval_id": "appr_risk",
                        "action_args": {"command": "echo " + ("x" * 600)},
                    }
                )
            )

        approved = asyncio.run(
            runtime.execute_action(
                {
                    "session_id": session_id,
                    "action": "run_command",
                    "risk_policy": {"red_policy": "owner_approval"},
                    "policy_metadata": {"owner_role": "owner", "owner_is_admin": True},
                    "approval_id": "appr_risk",
                    "cost_estimate_approved": True,
                    "action_args": {"command": "echo " + ("x" * 600)},
                }
            )
        )
        self.assertTrue(bool(((approved.get("action_result") or {}).get("cost_estimate") or {}).get("estimated_cost")))

    def test_threshold_pause_requires_budget_override_to_resume(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "cost_quota": {
                        "estimated_action_cost": 6.0,
                        "per_session_cost_limit": 10.0,
                        "budget_threshold_ratio": 0.5,
                        "budget_threshold_action": "pause",
                    },
                }
            )
        )
        session_id = created.get("session_id")

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "screenshot",
                        "action_args": {},
                    }
                )
            )

        with self.assertRaises(RuntimeError):
            asyncio.run(runtime.resume_session({"session_id": session_id}))

        resumed = asyncio.run(
            runtime.resume_session(
                {
                    "session_id": session_id,
                    "budget_override_approved": True,
                }
            )
        )
        self.assertEqual(resumed.get("state"), "running")

    def test_agent_runtime_budget_can_terminate_session(self):
        runtime = InMemoryVirtualComputerRuntime()
        created = asyncio.run(
            runtime.create_session(
                {
                    "runtime_choice": "virtual_browser",
                    "agent_id": "agent_a",
                    "cost_quota": {
                        "agent_runtime_budget_seconds": 1,
                        "estimated_action_cost": 0.1,
                    },
                }
            )
        )
        session_id = created.get("session_id")
        runtime._cost_by_session[session_id].started_at_epoch -= 5.0

        with self.assertRaises(RuntimeError):
            asyncio.run(
                runtime.execute_action(
                    {
                        "session_id": session_id,
                        "action": "screenshot",
                        "action_args": {},
                    }
                )
            )


if __name__ == "__main__":
    unittest.main()
