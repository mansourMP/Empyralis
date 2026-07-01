"""File-based encrypted credential vault — Fernet + PBKDF2.

Port of v1 patterns from legacy/server_modules/vault_store.py, simplified:
- No Rust kernel gates
- No cloud backup
- No multi-tenancy
- Single vault at ~/.empyralis/v2/vault.json.encrypted
"""

import base64
import json
import os
import secrets
from pathlib import Path
from typing import Any

VAULT_PATH = Path.home() / ".empyralis" / "v2" / "vault.json.encrypted"
VAULT_PASSPHRASE_ENV = "EMPYRALIS_VAULT_PASSPHRASE"
KDF_ITERATIONS = 600_000  # ~1s on modern hardware
CIPHER_PREFIX = "ES2:"


# ── crypto ────────────────────────────────────────────────────────────────────


def _crypto():
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    return Fernet, hashes, PBKDF2HMAC


def _derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    if not passphrase.strip():
        raise RuntimeError("Vault passphrase is empty.")
    _, hashes, PBKDF2HMAC = _crypto()
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=iterations)
    return base64.urlsafe_b64encode(kdf.derive(passphrase.encode("utf-8")))


def _encrypt(plaintext: str, passphrase: str) -> str:
    salt = secrets.token_bytes(16)
    key = _derive_key(passphrase, salt, KDF_ITERATIONS)
    Fernet, _, _ = _crypto()
    token = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    payload = {"v": 2, "alg": "fernet-pbkdf2-sha256", "iter": KDF_ITERATIONS,
               "salt": base64.urlsafe_b64encode(salt).decode("ascii"), "ct": token}
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"{CIPHER_PREFIX}{encoded}"


def _decrypt(ciphertext: str, passphrase: str) -> str:
    if not ciphertext.startswith(CIPHER_PREFIX):
        raise RuntimeError("Unsupported vault ciphertext format.")
    encoded = ciphertext[len(CIPHER_PREFIX):].strip()
    payload = json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))
    salt = base64.urlsafe_b64decode(payload["salt"].encode("ascii"))
    iterations = int(payload.get("iter", KDF_ITERATIONS))
    Fernet, _, _ = _crypto()
    key = _derive_key(passphrase, salt, iterations)
    return Fernet(key).decrypt(payload["ct"].encode("utf-8")).decode("utf-8")


# ── passphrase resolution ─────────────────────────────────────────────────────


def _resolve_passphrase() -> str:
    env_val = (os.getenv(VAULT_PASSPHRASE_ENV) or "").strip()
    if env_val:
        return env_val
    # Dev default: auto-generate and persist
    key_file = Path.home() / ".empyralis" / "v2" / ".vault_key"
    if key_file.exists():
        return key_file.read_text().strip()
    key_file.parent.mkdir(parents=True, exist_ok=True)
    generated = secrets.token_urlsafe(64)
    key_file.write_text(generated)
    key_file.chmod(0o600)
    return generated


# ── public API ─────────────────────────────────────────────────────────────────


def load_vault() -> dict[str, Any]:
    if not VAULT_PATH.exists():
        return {"version": 1, "credentials": []}
    raw = VAULT_PATH.read_text()
    if not raw.strip():
        return {"version": 1, "credentials": []}
    return json.loads(raw)


def save_vault(vault: dict[str, Any]) -> None:
    VAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    VAULT_PATH.write_text(json.dumps(vault, ensure_ascii=False, indent=2))
    VAULT_PATH.chmod(0o600)


def set_credential(vault: dict[str, Any], credential_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Add or replace a credential in the vault. Encrypts the secret value."""
    normalized_id = credential_id.strip()
    if not normalized_id:
        raise ValueError("credential_id is required.")
    plain = json.dumps(payload, separators=(",", ":"))
    entry = {"id": normalized_id, "encrypted_secret": _encrypt(plain, _resolve_passphrase())}
    creds = vault.get("credentials", [])
    # Replace if exists
    for i, c in enumerate(creds):
        if isinstance(c, dict) and c.get("id") == normalized_id:
            creds[i] = entry
            break
    else:
        creds.append(entry)
    vault["credentials"] = creds
    return payload


def get_credential(vault: dict[str, Any], credential_id: str) -> dict[str, Any] | None:
    """Read a credential from vault (decrypted)."""
    normalized_id = credential_id.strip()
    for c in vault.get("credentials", []):
        if isinstance(c, dict) and c.get("id") == normalized_id:
            plain = _decrypt(c["encrypted_secret"], _resolve_passphrase())
            return json.loads(plain)
    return None


def delete_credential(vault: dict[str, Any], credential_id: str) -> bool:
    """Remove a credential from the vault. Returns True if found and removed."""
    normalized_id = credential_id.strip()
    creds = vault.get("credentials", [])
    for i, c in enumerate(creds):
        if isinstance(c, dict) and c.get("id") == normalized_id:
            del creds[i]
            vault["credentials"] = creds
            return True
    return False


def credential_id(*, scope: str, provider: str, kind: str = "mcp") -> str:
    """Build a canonical credential ID.

    scope semantics (INTENTIONAL isolation — do not unify):
      - ``"global"``                    → ``f"{kind}:{provider}"``                (CLI)
      - ``"workspace:<workspace_id>"``  → ``f"workspace:<ws>:{kind}:{provider}"`` (web, Telegram — both workspace-scoped)
    """
    if scope == "global":
        return f"{kind}:{provider}"
    return f"{scope}:{kind}:{provider}"
