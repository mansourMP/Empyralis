import unittest

from server_modules import execution_mode_policy


class ExecutionModePolicyTests(unittest.TestCase):
    def test_full_access_is_available_for_dedicated_runtime_targets(self) -> None:
        cloud_modes = {
            item["id"]: item
            for item in execution_mode_policy.mode_contract_for_target("sage_cloud_computer")
        }
        local_modes = {
            item["id"]: item
            for item in execution_mode_policy.mode_contract_for_target("local_companion")
        }
        self_hosted_modes = {
            item["id"]: item
            for item in execution_mode_policy.mode_contract_for_target("self_host_runtime")
        }

        self.assertTrue(cloud_modes["full_access"]["available"])
        self.assertEqual(cloud_modes["full_access"]["label"], "Full Access")
        self.assertTrue(cloud_modes["autopilot"]["available"])
        self.assertTrue(cloud_modes["custom"]["available"])
        self.assertEqual(cloud_modes["custom"]["runtime_access_mode"], "custom")
        self.assertTrue(cloud_modes["custom"]["destructive_actions_require_approval"])
        self.assertFalse(cloud_modes["full_access"]["destructive_actions_require_approval"])
        self.assertTrue(local_modes["full_access"]["available"])
        self.assertTrue(local_modes["full_access"]["requires_owner_approval"])
        self.assertTrue(local_modes["custom"]["requires_owner_approval"])
        self.assertTrue(self_hosted_modes["full_access"]["available"])
        self.assertEqual(
            self_hosted_modes["full_access"]["runtime_access_mode"],
            execution_mode_policy.FULL_RUNTIME_ACCESS_MODE,
        )

    def test_summary_declares_safety_boundary(self) -> None:
        summary = execution_mode_policy.routing_contract_summary()

        self.assertEqual(summary["default_guarded_public_label"], "Default")
        self.assertEqual(summary["custom_public_label"], "Custom")
        self.assertEqual(summary["full_access_public_label"], "Full Access")
        self.assertEqual(summary["autonomous_full_access_runtime_access_mode"], "full_access")
        self.assertEqual(summary["full_access_scope"], "dedicated_runtime_targets")
        self.assertEqual(summary["destructive_actions_require_approval"], "default_guarded_and_custom")
        self.assertIn("autopilot", summary["supported_execution_modes"])
        self.assertIn("custom", summary["supported_execution_modes"])
        self.assertIn("full_access", summary["supported_execution_modes"])
        self.assertIn("custom", summary["supported_runtime_access_modes"])
        self.assertIn("full_access", summary["supported_runtime_access_modes"])

    def test_runtime_access_mode_fails_closed_for_full_access_aliases(self) -> None:
        for alias in (
            "autonomous_agent",
            "Autonomous Agent",
            "Autonomous Full Access",
            "dedicated-agent",
            "agent_owned_runtime",
        ):
            with self.subTest(alias=alias):
                self.assertEqual(
                    execution_mode_policy.normalize_runtime_access_mode(alias),
                    execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE,
                )
                self.assertEqual(
                    execution_mode_policy.runtime_access_mode_for_execution_mode(alias),
                    execution_mode_policy.GUARDED_RUNTIME_ACCESS_MODE,
                )

    def test_runtime_access_public_helpers_keep_internal_mode_stable(self) -> None:
        self.assertEqual(
            execution_mode_policy.public_runtime_access_label("full_access"),
            "Full Access",
        )
        self.assertEqual(execution_mode_policy.public_runtime_access_label("custom"), "Custom")
        self.assertIn(
            "Full Access lets Sage run commands",
            execution_mode_policy.runtime_access_setup_warning("full_access") or "",
        )
        self.assertIn(
            "dedicated hardware is recommended",
            execution_mode_policy.runtime_access_setup_warning("full_access") or "",
        )
        self.assertEqual(execution_mode_policy.public_runtime_access_label("default_guarded"), "Default")
        self.assertIsNone(execution_mode_policy.runtime_access_setup_warning("custom"))
        self.assertIsNone(execution_mode_policy.runtime_access_setup_warning("default_guarded"))


if __name__ == "__main__":
    unittest.main()
