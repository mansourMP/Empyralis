import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server_modules import jwt_secret


class JwtSecretResolutionTests(unittest.TestCase):
    def test_explicit_secret_wins_over_persisted_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "jwt_secret"
            secret_file.write_text("persisted-secret\n", encoding="utf-8")
            with (
                patch.object(jwt_secret, "JWT_SECRET_FILE", secret_file),
                patch.object(jwt_secret, "_JWT_SECRET_CACHE", None),
                patch.dict("os.environ", {"ORION_JWT_SECRET": "explicit-secret"}, clear=True),
            ):
                resolved = jwt_secret.resolve_jwt_secret()

        self.assertEqual(resolved, "explicit-secret")

    def test_resolve_jwt_secret_does_not_seed_from_orion_api_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_file = Path(tmp) / "jwt_secret"
            with (
                patch.object(jwt_secret, "JWT_SECRET_FILE", secret_file),
                patch.object(jwt_secret, "_JWT_SECRET_CACHE", None),
                patch.dict("os.environ", {"ORION_API_KEY": "api-key-secret"}, clear=True),
            ):
                resolved = jwt_secret.resolve_jwt_secret()
                persisted = secret_file.read_text(encoding="utf-8").strip()

        self.assertNotEqual(resolved, "api-key-secret")
        self.assertNotEqual(persisted, "api-key-secret")

    def test_production_requires_explicit_non_placeholder_secret(self):
        with patch.dict("os.environ", {"ORION_ENV": "production"}, clear=True):
            self.assertFalse(jwt_secret.explicit_secret_is_safe_for_environment())
            with self.assertRaises(RuntimeError):
                jwt_secret.assert_explicit_secret_safe_for_environment()

    def test_production_rejects_short_or_placeholder_secret(self):
        for secret in ("short", "replace-with-a-random-32-plus-character-secret"):
            with patch.dict("os.environ", {"ORION_ENV": "production", "ORION_JWT_SECRET": secret}, clear=True):
                self.assertFalse(jwt_secret.explicit_secret_is_safe_for_environment())

    def test_production_accepts_long_explicit_secret(self):
        with patch.dict(
            "os.environ",
            {"ORION_ENV": "production", "ORION_JWT_SECRET": "x" * 40},
            clear=True,
        ):
            self.assertTrue(jwt_secret.explicit_secret_is_safe_for_environment())


if __name__ == "__main__":
    unittest.main()
