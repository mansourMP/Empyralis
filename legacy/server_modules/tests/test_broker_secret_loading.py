import unittest
from unittest.mock import patch

from server_modules import secrets_broker, tool_broker


class BrokerSecretLoadingTests(unittest.TestCase):
    def test_explicit_secrets_are_used_when_configured(self):
        with patch.dict(
            "os.environ",
            {
                "EMPYRALIS_SECRETS_BROKER_SECRET": "secret-broker-secret",
                "EMPYRALIS_TOOL_BROKER_SECRET": "tool-broker-secret",
            },
            clear=True,
        ):
            self.assertEqual(secrets_broker._signing_secret(), b"secret-broker-secret")
            self.assertEqual(tool_broker._signing_secret(), b"tool-broker-secret")

    def test_secrets_broker_requires_explicit_secret_in_production(self):
        with patch.dict("os.environ", {"ORION_ENV": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                secrets_broker._signing_secret()

    def test_tool_broker_requires_explicit_secret_in_production(self):
        with patch.dict("os.environ", {"ORION_ENV": "production"}, clear=True):
            with self.assertRaises(RuntimeError):
                tool_broker._signing_secret()


if __name__ == "__main__":
    unittest.main()
