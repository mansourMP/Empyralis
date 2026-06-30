from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from server_modules import jwt_secret, vault_store


_ALLOW_DECISION = {
    "ok": True,
    "decision": "allow",
    "reason": "runtime_state_store_policy_satisfied",
    "operation": "runtime-state-store-decision",
    "next_action": "write_jwt_secret_file",
    "approval_required": False,
    "cacheable": False,
    "audit_visibility": "standard",
}


class SecretMaterialRustGateTests(unittest.TestCase):
    def test_jwt_secret_write_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jwt_secret"
            with mock.patch.object(
                jwt_secret.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=dict(_ALLOW_DECISION),
            ) as rust_decision:
                result = jwt_secret._write_secret_file(path, "x" * 48)

            self.assertEqual(result, "x" * 48)
            self.assertTrue(path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_jwt_secret_file")
            self.assertEqual(payload["state_class"], "secret_material")
            self.assertEqual(payload["payload"]["secret_length"], 48)

    def test_jwt_secret_write_blocks_before_file_write_when_rust_blocks(self) -> None:
        block_decision = {
            **_ALLOW_DECISION,
            "ok": False,
            "decision": "block",
            "reason": "owner_access_denied",
            "operation": "write_jwt_secret_file",
            "audit_visibility": "security",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jwt_secret"
            with mock.patch.object(
                jwt_secret.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=block_decision,
            ):
                with self.assertRaises(jwt_secret.JwtSecretRustGateError):
                    jwt_secret._write_secret_file(path, "x" * 48)

            self.assertFalse(path.exists())

    def test_jwt_secret_write_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        wrong_action = {
            **_ALLOW_DECISION,
            "next_action": "write_profile_api_file",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "jwt_secret"
            with mock.patch.object(
                jwt_secret.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value=wrong_action,
            ):
                with self.assertRaises(jwt_secret.JwtSecretRustGateError) as raised:
                    jwt_secret._write_secret_file(path, "x" * 48)

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(path.exists())

    def test_vault_key_rotation_calls_rust_before_persisting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "vault_key"
            fake_server = SimpleNamespace(VAULT_KEY_ENV="", VAULT_KEY_FILE=path)
            with mock.patch.object(vault_store, "_server", fake_server), mock.patch.object(
                vault_store,
                "_init",
                return_value=None,
            ), mock.patch.object(
                vault_store.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "write_vault_key_file"},
            ) as rust_decision:
                vault_store._set_vault_passphrase("secret-passphrase")

            self.assertTrue(path.exists())
            payload = rust_decision.call_args.kwargs
            self.assertEqual(payload["operation"], "write_vault_key_file")
            self.assertEqual(payload["state_class"], "secret_material")
            self.assertEqual(payload["payload"]["action"], "rotate")
            self.assertEqual(payload["payload"]["secret_length"], len("secret-passphrase"))

    def test_vault_key_rotation_blocks_before_file_write_on_wrong_rust_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "vault_key"
            fake_server = SimpleNamespace(VAULT_KEY_ENV="", VAULT_KEY_FILE=path)
            with mock.patch.object(vault_store, "_server", fake_server), mock.patch.object(
                vault_store,
                "_init",
                return_value=None,
            ), mock.patch.object(
                vault_store.rust_runtime_kernel_client,
                "runtime_state_store_decision",
                return_value={**_ALLOW_DECISION, "next_action": "write_profile_api_file"},
            ):
                with self.assertRaises(vault_store.VaultStoreRustGateError) as raised:
                    vault_store._set_vault_passphrase("secret-passphrase")

            self.assertEqual(str(raised.exception), "unexpected_next_action")
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
