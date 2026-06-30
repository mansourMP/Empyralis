import unittest

from server_modules import agent_registry_repository


class AgentRegistryRepositoryContractTests(unittest.TestCase):
    def test_default_seed_does_not_publish_named_specialists(self) -> None:
        self.assertEqual(agent_registry_repository.DEFAULT_AGENT_DEFINITIONS, [])
        self.assertEqual(agent_registry_repository.DEFAULT_MASTER_AGENT_DEFINITION["slug"], "sage")
        self.assertTrue(agent_registry_repository.DEFAULT_MASTER_AGENT_DEFINITION.get("visibility") == "private")

    def test_captain_metadata_normalization_keeps_stable_id_and_editable_display_name(self) -> None:
        metadata = agent_registry_repository.normalize_install_contract_metadata(
            install_id="ainstall_sage",
            label="Mansur",
            agent_kind="master",
            metadata={"system_agent": True},
        )

        self.assertEqual(metadata["captain_profile"]["display_name"], "Mansur")
        self.assertEqual(metadata["captain_profile"]["stable_internal_id"], "ainstall_sage")
        self.assertTrue(metadata["captain_profile"]["editable_display_name"])
        self.assertEqual(metadata["captain_profile"]["role"], "private_main_agent")
        self.assertNotIn("specialist_mode", metadata)

    def test_captain_metadata_rejects_specialist_mode_fields(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            agent_registry_repository.normalize_install_contract_metadata(
                install_id="ainstall_sage",
                label="Captain",
                agent_kind="master",
                metadata={"specialist_mode": "owner_test"},
            )

        self.assertIn("Captain installs do not support", str(ctx.exception))

    def test_specialist_metadata_normalization_persists_mode_contract(self) -> None:
        metadata = agent_registry_repository.normalize_install_contract_metadata(
            install_id="ainstall_support",
            label="Support",
            agent_kind="specialist",
            metadata={"specialist_mode": "customer_live"},
        )

        self.assertEqual(metadata["specialist_mode"], "customer_live")
        self.assertEqual(metadata["specialist_mode_contract"]["mode"], "customer_live")
        self.assertFalse(metadata["specialist_mode_contract"]["prompt_editable"])
        self.assertEqual(metadata["specialist_mode_contract"]["response_audience"], "external_customer")
        self.assertNotIn("captain_profile", metadata)

    def test_specialist_metadata_rejects_invalid_mode(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            agent_registry_repository.normalize_install_contract_metadata(
                install_id="ainstall_support",
                label="Support",
                agent_kind="specialist",
                metadata={"specialist_mode": "bad_mode"},
            )

        self.assertIn("Unsupported specialist_mode", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
