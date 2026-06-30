import unittest

from server_modules.builder_runtime_mapping import map_builder_permissions_to_runtime_metadata


class BuilderRuntimeMappingTests(unittest.TestCase):
    def test_runtime_metadata_includes_trust_preset_and_remembered_grants(self):
        metadata = map_builder_permissions_to_runtime_metadata(
            {
                "trust_preset": "trusted_workflow",
                "remembered_grants": {
                    "folders": ["/Users/mansur/Downloads"],
                    "browser_session": True,
                    "shell_capabilities": ["stack.status"],
                },
            },
            execution_target="local_companion",
        )

        self.assertEqual(metadata["trust_preset"], "trusted_workflow")
        self.assertEqual(
            metadata["remembered_grants"],
            {
                "folders": ["/Users/mansur/Downloads"],
                "browser_session": True,
                "shell_capabilities": ["stack.status"],
            },
        )


if __name__ == "__main__":
    unittest.main()
