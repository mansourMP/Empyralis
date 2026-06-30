import unittest

from server_modules.connectors.autopilot_support_service_registry import AutopilotSupportServiceRegistry


class AutopilotSupportServiceRegistryTests(unittest.TestCase):
    def test_registry_caches_support_services(self) -> None:
        counters = {
            "profile": 0,
            "runtime": 0,
            "workflow": 0,
            "context": 0,
            "approval": 0,
            "common": 0,
            "skill": 0,
            "channel": 0,
        }

        def _build(name: str):
            def _factory():
                counters[name] += 1
                return object()

            return _factory

        registry = AutopilotSupportServiceRegistry(
            build_profile_service=_build("profile"),
            build_runtime_status_service=_build("runtime"),
            build_workflow_setup_service=_build("workflow"),
            build_connector_context_service=_build("context"),
            build_approval_service=_build("approval"),
            build_common_support_service=_build("common"),
            build_skill_service=_build("skill"),
            build_channel_support_service=_build("channel"),
        )

        self.assertIs(registry.profile_service(), registry.profile_service())
        self.assertIs(registry.runtime_status_service(), registry.runtime_status_service())
        self.assertIs(registry.workflow_setup_service(), registry.workflow_setup_service())
        self.assertIs(registry.connector_context_service(), registry.connector_context_service())
        self.assertIs(registry.approval_service(), registry.approval_service())
        self.assertIs(registry.common_support_service(), registry.common_support_service())
        self.assertIs(registry.skill_service(), registry.skill_service())
        self.assertIs(registry.channel_support_service(), registry.channel_support_service())
        self.assertEqual(counters, {key: 1 for key in counters})


if __name__ == "__main__":
    unittest.main()
