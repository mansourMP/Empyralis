import unittest
from unittest.mock import patch

from server_modules import runtime_policy


class VirtualComputerRoutingPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_unknown_or_untrusted_work_routes_virtual_first(self):
        decision = runtime_policy.decide_runtime_choice({"unknown_website": True})
        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        self.assertEqual(decision.get("source"), "router_rule_virtual_browser_untrusted")

    def test_explicit_override_returns_policy_warning(self):
        decision = runtime_policy.decide_runtime_choice(
            {
                "runtime_choice": "local",
                "risky_web_automation": True,
            }
        )
        self.assertTrue(decision.get("override_requested"))
        self.assertTrue(bool(decision.get("policy_warning")))

    def test_local_unavailable_falls_back_to_cloud_only_when_allowed(self):
        with patch.object(runtime_policy, "ORION_LOCAL_COMPANION_ENABLED", False):
            allowed = runtime_policy.decide_execution_target(
                {
                    "execution_target": "local_companion",
                    "trust_mode": runtime_policy.TRUST_MODE_AUTO,
                }
            )
            self.assertEqual(allowed.get("selected"), runtime_policy.EXECUTION_TARGET_CLOUD)

            blocked = runtime_policy.decide_execution_target(
                {
                    "execution_target": "local_companion",
                    "trust_mode": runtime_policy.TRUST_MODE_GUARDED,
                }
            )
            self.assertEqual(blocked.get("selected"), runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION)
            self.assertTrue(bool(blocked.get("waiting_for_runtime")))

    def test_virtual_unavailable_falls_back_to_local_only_for_low_risk(self):
        low_risk = runtime_policy.decide_execution_target(
            {
                "runtime_choice": "virtual_browser",
                "virtual_runtime_unavailable": True,
            }
        )
        self.assertEqual(low_risk.get("selected"), runtime_policy.EXECUTION_TARGET_LOCAL_COMPANION)

        high_risk = runtime_policy.decide_execution_target(
            {
                "runtime_choice": "virtual_browser",
                "virtual_runtime_unavailable": True,
                "unknown_website": True,
            }
        )
        self.assertEqual(high_risk.get("selected"), runtime_policy.EXECUTION_TARGET_CLOUD)
        self.assertTrue(bool(high_risk.get("waiting_for_runtime")))


if __name__ == "__main__":
    unittest.main()
