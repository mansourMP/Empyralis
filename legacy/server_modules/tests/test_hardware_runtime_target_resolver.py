from __future__ import annotations

import unittest

from server_modules import hardware_runtime_target_resolver as resolver


class HardwareRuntimeTargetResolverTests(unittest.TestCase):
    def test_legacy_targets_resolve_to_canonical_runtime_targets(self) -> None:
        cases = {
            "cloud_default": ("cloud_default", "Cloud", "Cloud"),
            "local_companion": ("user_device_gateway", "Agent Computer", "This Device"),
            "user_device_gateway": ("user_device_gateway", "Agent Computer", "This Device"),
            "sage_cloud_computer": ("empyralis_cloud_computer", "Agent Computer", "Cloud Computer"),
            "empyralis_cloud_computer": ("empyralis_cloud_computer", "Agent Computer", "Cloud Computer"),
            "self_host_runtime": ("self_hosted_node", "Agent Computer", "Server/VPS"),
            "self_hosted_node": ("self_hosted_node", "Agent Computer", "Server/VPS"),
        }
        for runtime_target, (canonical, group, source) in cases.items():
            with self.subTest(runtime_target=runtime_target):
                resolved = resolver.resolve_runtime_target(runtime_target)
                self.assertEqual(resolved.canonical_runtime_target, canonical)
                self.assertEqual(resolved.group_label, group)
                self.assertEqual(resolved.source_label, source)

    def test_runtime_target_ids_preserve_legacy_ingress_and_canonical_fabric_target(self) -> None:
        self.assertEqual(
            resolver.runtime_target_ids("sage_cloud_computer"),
            {
                "runtime_target": "sage_cloud_computer",
                "canonical_runtime_target": "empyralis_cloud_computer",
            },
        )

    def test_local_hardware_action_uses_gateway_or_offline_error_when_target_missing(self) -> None:
        live = resolver.resolve_runtime_target(
            None,
            action_type="shell.execute",
            selected_gateway_live=True,
        )
        self.assertEqual(live.canonical_runtime_target, "user_device_gateway")
        self.assertEqual(live.execution_environment, "local_gateway")
        self.assertEqual(live.error, "")

        offline = resolver.resolve_runtime_target(
            None,
            action_type="filesystem.read_write",
            selected_gateway_live=False,
        )
        self.assertEqual(offline.canonical_runtime_target, "cloud_default")
        self.assertEqual(offline.execution_environment, "cloud_provider")
        self.assertEqual(offline.error, resolver.AGENT_COMPUTER_OFFLINE_ERROR)


if __name__ == "__main__":
    unittest.main()
