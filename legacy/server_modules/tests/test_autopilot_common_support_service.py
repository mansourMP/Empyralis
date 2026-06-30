import unittest
from pathlib import Path

from server_modules.connectors.autopilot_common_support_service import AutopilotCommonSupportService


class AutopilotCommonSupportServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> AutopilotCommonSupportService:
        env_map = overrides.pop("env_map", {})
        return AutopilotCommonSupportService(
            project_root=overrides.pop("project_root", Path("/tmp/empyralis")),
            env_get=overrides.pop("env_get", lambda key, default="": env_map.get(key, default)),
            import_cognitive_module=overrides.pop("import_cognitive_module", lambda: object()),
        )

    def test_cognitive_defaults_uses_override_or_state_home(self) -> None:
        state_home = "/tmp/empyralis-state"
        service = self._make_service(env_map={"ORION_COGNITIVE_NICHE_ID": "biology", "EMPYRALIS_STATE_HOME": state_home})
        defaults = service.cognitive_defaults()
        self.assertEqual(defaults["niche_id"], "biology")
        self.assertEqual(defaults["db_path"], f"{state_home}/memory/agency_memory.db")

        override = self._make_service(env_map={"ORION_COGNITIVE_DB_PATH": "/tmp/custom.db"})
        self.assertEqual(override.cognitive_defaults()["db_path"], "/tmp/custom.db")

    def test_cognitive_module_returns_none_on_import_failure(self) -> None:
        service = self._make_service(import_cognitive_module=lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertIsNone(service.cognitive_module())

    def test_normalize_string_list_and_chat_id_from_session_key(self) -> None:
        service = self._make_service()
        self.assertEqual(service.normalize_string_list([" a ", "", None, "b"]), ["a", "b"])
        self.assertEqual(service.normalize_string_list("bad"), [])
        self.assertEqual(service.chat_id_from_session_key("telegram:123"), "123")
        self.assertEqual(service.chat_id_from_session_key("other:123"), "")


if __name__ == "__main__":
    unittest.main()
