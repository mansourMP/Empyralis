import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from server_modules import vault_store


class VaultStoreTests(unittest.TestCase):
    def test_vault_never_passes_secrets_through_process_arguments(self) -> None:
        source = inspect.getsource(vault_store)
        self.assertNotIn("subprocess", source)
        self.assertNotIn("pass:", source)
        self.assertNotIn("-pass", source)

    def test_legacy_openssl_paths_are_disabled(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            vault_store._legacy_openssl_encrypt_with_passphrase("secret", "vault-key")
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            vault_store._legacy_openssl_decrypt_with_passphrase("ciphertext", "vault-key")

    def test_vault_passphrase_requires_env_key_outside_local_dev(self) -> None:
        original_server = vault_store._server
        with TemporaryDirectory() as tmpdir:
            vault_store._server = SimpleNamespace(
                VAULT_KEY_ENV="",
                VAULT_KEY_FILE=Path(tmpdir) / "vault.key",
            )
            try:
                with patch.dict("os.environ", {"ORION_ENV": "beta"}, clear=True):
                    with self.assertRaises(RuntimeError):
                        vault_store._vault_passphrase()
            finally:
                vault_store._server = original_server

    def test_vault_passphrase_generates_local_key_when_env_is_local(self) -> None:
        original_server = vault_store._server
        with TemporaryDirectory() as tmpdir:
            key_file = Path(tmpdir) / "vault.key"
            vault_store._server = SimpleNamespace(
                VAULT_KEY_ENV="",
                VAULT_KEY_FILE=key_file,
            )
            try:
                with patch.dict("os.environ", {"ORION_ENV": "local"}, clear=True):
                    passphrase = vault_store._vault_passphrase()
                    self.assertTrue(passphrase)
                    self.assertTrue(key_file.exists())
            finally:
                vault_store._server = original_server
