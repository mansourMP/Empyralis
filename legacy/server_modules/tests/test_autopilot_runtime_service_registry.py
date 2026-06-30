import unittest

from server_modules.connectors.autopilot_runtime_service_registry import AutopilotRuntimeServiceRegistry


class AutopilotRuntimeServiceRegistryTests(unittest.TestCase):
    def test_registry_caches_runtime_services(self) -> None:
        counters = {
            "connector": 0,
            "transport": 0,
            "terminal": 0,
            "run_entry": 0,
            "runtime_support": 0,
            "menu": 0,
        }

        def _build(name: str):
            def _factory():
                counters[name] += 1
                return object()

            return _factory

        registry = AutopilotRuntimeServiceRegistry(
            build_connector_support_service=_build("connector"),
            build_transport_service=_build("transport"),
            build_terminal_service=_build("terminal"),
            build_run_entry_service=_build("run_entry"),
            build_runtime_support_service=_build("runtime_support"),
            build_menu_service=_build("menu"),
        )

        self.assertIs(registry.connector_support_service(), registry.connector_support_service())
        self.assertIs(registry.transport_service(), registry.transport_service())
        self.assertIs(registry.terminal_service(), registry.terminal_service())
        self.assertIs(registry.run_entry_service(), registry.run_entry_service())
        self.assertIs(registry.runtime_support_service(), registry.runtime_support_service())
        self.assertIs(registry.menu_service(), registry.menu_service())
        self.assertEqual(counters, {key: 1 for key in counters})


if __name__ == "__main__":
    unittest.main()
