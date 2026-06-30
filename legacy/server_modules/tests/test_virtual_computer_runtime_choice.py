import unittest

from server_modules import runtime_policy


class VirtualComputerRuntimeChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_explicit_runtime_choice_virtual_browser(self):
        decision = runtime_policy.decide_runtime_choice({"runtime_choice": "virtual_browser"})

        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        self.assertEqual(decision.get("source"), "explicit")

    def test_personal_session_routes_local(self):
        decision = runtime_policy.decide_runtime_choice({"browser_session_mode": "existing_session_attach"})

        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_LOCAL)
        self.assertEqual(decision.get("source"), "router_rule_personal_local")

    def test_risky_browser_automation_routes_virtual_browser(self):
        decision = runtime_policy.decide_runtime_choice(
            {
                "pack_inputs": {
                    "operations": [
                        {"tool": "browser_automation.interactive"},
                    ]
                },
                "browser_automation_policy": {"requires_approval": True},
            }
        )

        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        self.assertEqual(decision.get("source"), "router_rule_virtual_browser")

    def test_code_job_routes_virtual_code_sandbox(self):
        decision = runtime_policy.decide_runtime_choice({"outcome_pack": runtime_policy.LOCAL_EXECUTION_PACK_ID})

        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX)
        self.assertEqual(decision.get("source"), "router_rule_virtual_code_sandbox")

    def test_customer_workflow_routes_virtual_desktop(self):
        decision = runtime_policy.decide_runtime_choice({"outcome_pack": runtime_policy.CUSTOMER_OPS_PACK_ID})

        self.assertEqual(decision.get("selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_DESKTOP)
        self.assertEqual(decision.get("source"), "router_rule_virtual_desktop")

    def test_execution_target_uses_runtime_choice_when_auto(self):
        route = runtime_policy.decide_execution_target({"runtime_choice": "virtual_code_sandbox"})

        self.assertEqual(route.get("requested"), runtime_policy.EXECUTION_TARGET_CLOUD)
        self.assertEqual(route.get("runtime_choice_selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX)
        self.assertTrue(route.get("runtime_choice_applied"))


if __name__ == "__main__":
    unittest.main()
