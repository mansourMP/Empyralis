import unittest

from server_modules import runtime_policy


class VirtualComputerAgentStudioIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        runtime_policy._init()

    def test_studio_runtime_options_are_exposed(self):
        route = runtime_policy.decide_execution_target(
            {
                "template_requirements": {
                    "needs_browser": True,
                }
            }
        )
        options = route.get("studio_runtime_options") or []
        option_ids = {str(item.get("option_id") or "") for item in options if isinstance(item, dict)}
        self.assertIn("managed_cloud", option_ids)
        self.assertIn("customer_local", option_ids)
        self.assertIn("customer_hosted", option_ids)
        self.assertIn("computer_automation_add_on", option_ids)

    def test_template_requirements_route_runtime_choice(self):
        browser_route = runtime_policy.decide_execution_target(
            {
                "template_requirements": {
                    "needs_browser": True,
                    "needs_website_login": True,
                    "needs_approval": True,
                }
            }
        )
        self.assertEqual(browser_route.get("runtime_choice_selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        requirements = browser_route.get("studio_template_requirements") or {}
        self.assertTrue(requirements.get("needs_browser"))
        self.assertTrue(requirements.get("needs_website_login"))
        self.assertTrue(requirements.get("needs_approval"))

        files_route = runtime_policy.decide_execution_target(
            {
                "template_requirements": {
                    "needs_files": True,
                }
            }
        )
        self.assertEqual(files_route.get("runtime_choice_selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_CODE_SANDBOX)

    def test_shop_assistant_supplier_portal_routes_virtual_browser(self):
        route = runtime_policy.decide_execution_target(
            {
                "template_slug": "shop-assistant",
                "supplier_portal": True,
                "order_status_workflow": True,
            }
        )
        self.assertEqual(route.get("runtime_choice_selected"), runtime_policy.RUNTIME_CHOICE_VIRTUAL_BROWSER)
        self.assertEqual(route.get("selected"), runtime_policy.EXECUTION_TARGET_CLOUD)
        self.assertIn("supplier portal", str(route.get("runtime_choice_reason") or "").lower())


if __name__ == "__main__":
    unittest.main()
