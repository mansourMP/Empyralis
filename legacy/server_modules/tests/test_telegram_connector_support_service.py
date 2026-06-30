import unittest

from server_modules.connectors.telegram_connector_support_service import TelegramConnectorSupportService


class TelegramConnectorSupportServiceTests(unittest.TestCase):
    def _make_service(self, **overrides) -> TelegramConnectorSupportService:
        return TelegramConnectorSupportService(
            normalize_workspace_id=overrides.pop("normalize_workspace_id", lambda value: str(value or "").strip()),
            resolve_vault_credential=overrides.pop(
                "resolve_vault_credential",
                lambda credential_id, workspace_id: {"id": credential_id, "workspace_id": workspace_id},
            ),
            normalize_agent_role=overrides.pop("normalize_agent_role", lambda value: str(value or "").strip().lower()),
            allow_any_chat=overrides.pop("allow_any_chat", False),
        )

    def test_bool_and_connector_metadata_helpers(self) -> None:
        service = self._make_service()
        entry = {"metadata": {"paused": "yes", "agent_role": "Operator"}}
        self.assertTrue(service.bool_from_any("yes"))
        self.assertEqual(service.connector_metadata(entry)["agent_role"], "Operator")
        self.assertEqual(service.connector_assigned_agent_role(entry), "operator")
        self.assertTrue(service.connector_paused(entry))

    def test_get_secret_requires_connector_id(self) -> None:
        service = self._make_service()
        with self.assertRaisesRegex(RuntimeError, "missing id"):
            service.get_secret({})
        secret = service.get_secret({"id": "cred-1", "workspace_id": "ws-1"})
        self.assertEqual(secret["id"], "cred-1")
        self.assertEqual(secret["workspace_id"], "ws-1")

    def test_chat_matches_respects_allow_any_and_specific_targets(self) -> None:
        any_chat = self._make_service(allow_any_chat=True)
        self.assertTrue(any_chat.chat_matches("", {"id": "123"}))

        service = self._make_service()
        self.assertTrue(service.chat_matches("@alpha", {"username": "alpha"}))
        self.assertTrue(service.chat_matches("123", {"id": "123"}))
        self.assertFalse(service.chat_matches("456", {"id": "123"}))

    def test_allow_from_parsing_and_sender_check(self) -> None:
        service = self._make_service()
        parsed = service.parse_allow_from("id:123, user:Alpha, beta, any")
        self.assertEqual(parsed, ["*"])

        resolved = service.resolve_allow_from(
            {"metadata": {"allow_from": "id:123", "telegram_allow_from": "user:alpha"}},
            "beta",
        )
        self.assertEqual(resolved, ["123", "@alpha", "@beta"])
        self.assertTrue(service.sender_allowed({"id": "123"}, resolved))
        self.assertTrue(service.sender_allowed({"username": "alpha"}, resolved))
        self.assertFalse(service.sender_allowed({"username": "gamma"}, resolved))


if __name__ == "__main__":
    unittest.main()
