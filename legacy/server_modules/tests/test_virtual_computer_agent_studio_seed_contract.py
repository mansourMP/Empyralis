import unittest

from server_modules import studio_proof_agent_seed_service


class VirtualComputerAgentStudioSeedContractTests(unittest.TestCase):
    def test_shop_assistant_declares_runtime_options_and_requirements(self):
        contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
        self.assertIsNotNone(contract)
        runtime_options = contract.get("studio_runtime_options") or []
        option_ids = {str(item.get("option_id") or "") for item in runtime_options if isinstance(item, dict)}
        self.assertIn("managed_cloud", option_ids)
        self.assertIn("customer_local", option_ids)
        self.assertIn("customer_hosted", option_ids)
        self.assertIn("computer_automation_add_on", option_ids)

        requirements = contract.get("template_runtime_requirements") or {}
        self.assertFalse(requirements.get("needs_browser"))
        self.assertFalse(requirements.get("needs_website_login"))
        self.assertTrue(requirements.get("needs_approval"))
        self.assertFalse(requirements.get("needs_computer_automation"))
        self.assertTrue(requirements.get("needs_api_connector"))
        self.assertTrue(requirements.get("needs_business_channel"))

    def test_template_inputs_schema_accepts_template_requirements(self):
        contract = studio_proof_agent_seed_service.get_studio_proof_agent_seed_contract("shop-assistant")
        schema = ((contract.get("customization") or {}).get("template_inputs_schema") or {})
        properties = schema.get("properties") or {}
        req_schema = properties.get("template_requirements") or {}
        req_props = req_schema.get("properties") or {}
        self.assertIn("needs_browser", req_props)
        self.assertIn("needs_files", req_props)
        self.assertIn("needs_website_login", req_props)
        self.assertIn("needs_payments", req_props)
        self.assertIn("needs_approval", req_props)
        self.assertIn("needs_computer_automation", req_props)
        self.assertIn("needs_api_connector", req_props)


if __name__ == "__main__":
    unittest.main()
