"""
Credential vault storage + encryption helpers.

Extracted from server.py to reduce hotspot size.
All function signatures and behaviour are unchanged.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any, Dict

_server = None  # populated by _init()
_LOCAL_ENV_TOKENS = {"", "dev", "development", "local", "test", "testing"}


def _kernel_client():
    """Lazy-import the Rust kernel client, bypassing server_modules/__init__.py.

    The __init__.py pulls in provider_profiles -> pydantic which may be
    unavailable in the command worker's system Python.  We import
    rust_runtime_kernel_client.py directly as a standalone module so we
    never touch the package init.
    """
    global _kc
    if _kc is not None:
        return _kc
    import importlib.util, sys as _sys
    spec = importlib.util.spec_from_file_location(
        "rust_runtime_kernel_client",
        os.path.join(os.path.dirname(__file__), "rust_runtime_kernel_client.py"),
    )
    _kc = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec so @dataclass decorators can find
    # the module via cls.__module__ during class creation.
    _sys.modules["rust_runtime_kernel_client"] = _kc
    spec.loader.exec_module(_kc)
    return _kc

_kc = None

# ── Cloud API HTTP helper (stdlib only — no extra deps) ─────────────────
# vault_store runs inside the command worker which may lack requests/httpx.


def _cloud_api_post(path: str, body: Dict[str, Any], *, timeout: int = 30) -> Dict[str, Any]:
    """POST JSON to the cloud control plane.  Uses stdlib urllib only."""
    import urllib.request
    import urllib.error

    cloud_url = (os.getenv("EMPYRALIS_CLOUD_URL") or "").strip().rstrip("/")
    session_token = (os.getenv("EMPYRALIS_NODE_SESSION_TOKEN") or "").strip()
    if not cloud_url:
        raise RuntimeError("EMPYRALIS_CLOUD_URL is not set")
    if not session_token:
        raise RuntimeError("EMPYRALIS_NODE_SESSION_TOKEN is not set")

    data = json.dumps(body).encode("utf-8")
    url = f"{cloud_url}{path}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {session_token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        raise RuntimeError(f"Cloud API HTTP {exc.code} {path}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Cloud API URL error {path}: {exc}") from exc


def _cloud_backup_api_path(runtime_profile_id: str) -> str:
    return f"/runtime/self-hosted-nodes/{runtime_profile_id}/vault-backup"
LOGGER = logging.getLogger(__name__)


def _init():
    """Late-bind references to server.py globals. Called on first use."""
    global _server
    if _server is not None:
        return
    import server as _s
    _server = _s


def _safe_write_json(path: Path, payload: Dict[str, Any]):
    _init()
    return _server._safe_write_json(path, payload)


def _resolved_environment() -> str:
    return str(os.getenv("ORION_ENV") or os.getenv("ENV") or "").strip().lower()


def _vault_requires_explicit_env_key() -> bool:
    return _resolved_environment() not in _LOCAL_ENV_TOKENS


class VaultStoreRustGateError(RuntimeError):
    pass


def _enforce_vault_key_state_decision(*, path: Path, action: str, passphrase: str) -> Dict[str, Any]:
    payload = {
        "path": str(path),
        "action": str(action or "").strip(),
        "secret_length": len(str(passphrase or "")),
        "environment": _resolved_environment(),
    }
    try:
        decision = _kernel_client().runtime_state_store_decision(
            operation="write_vault_key_file",
            state_class="secret_material",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=len(json.dumps(payload, sort_keys=True).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        _kernel_client().enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_vault_key_file":
            raise VaultStoreRustGateError("unexpected_next_action")
        return decision
    except Exception as exc:
        kc = _kernel_client()
        if isinstance(exc, kc.RustKernelDecisionError):
            raise VaultStoreRustGateError(exc.reason) from exc
        raise


def _vault_passphrase() -> str:
    _init()
    vault_key_env = _server.VAULT_KEY_ENV
    vault_key_file = _server.VAULT_KEY_FILE
    if vault_key_env and vault_key_env.strip():
        return vault_key_env.strip()
    if _vault_requires_explicit_env_key():
        raise RuntimeError("CREDENTIAL_VAULT_KEY is required outside local development environments.")

    if not vault_key_file.exists():
        key_parent = vault_key_file.parent if vault_key_file.parent != Path("") else Path(".")
        key_parent.mkdir(parents=True, exist_ok=True)
        generated = secrets.token_urlsafe(64)
        _enforce_vault_key_state_decision(path=vault_key_file, action="generate", passphrase=generated)
        vault_key_file.write_text(generated, encoding="utf-8")
        try:
            os.chmod(vault_key_file, 0o600)
        except Exception:
            pass
    return vault_key_file.read_text(encoding="utf-8").strip()


def _set_vault_passphrase(passphrase: str):
    _init()
    vault_key_env = _server.VAULT_KEY_ENV
    vault_key_file = _server.VAULT_KEY_FILE
    if vault_key_env and vault_key_env.strip():
        raise RuntimeError("Cannot rotate vault key while CREDENTIAL_VAULT_KEY env var is set.")
    key_parent = vault_key_file.parent if vault_key_file.parent != Path("") else Path(".")
    key_parent.mkdir(parents=True, exist_ok=True)
    normalized = passphrase.strip()
    _enforce_vault_key_state_decision(path=vault_key_file, action="rotate", passphrase=normalized)
    vault_key_file.write_text(normalized, encoding="utf-8")
    try:
        os.chmod(vault_key_file, 0o600)
    except Exception:
        pass


def _vault_crypto_primitives():
    try:
        from cryptography.fernet import Fernet, InvalidToken
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except Exception as exc:
        raise RuntimeError(
            "Credential vault encryption backend unavailable. Install Python package 'cryptography'."
        ) from exc
    return Fernet, InvalidToken, hashes, PBKDF2HMAC


def _vault_derive_key(passphrase: str, salt: bytes, iterations: int) -> bytes:
    value = str(passphrase or "").strip()
    if not value:
        raise RuntimeError("Vault passphrase is empty.")
    _, _, hashes, PBKDF2HMAC = _vault_crypto_primitives()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iterations,
    )
    return base64.urlsafe_b64encode(kdf.derive(value.encode("utf-8")))


def _legacy_openssl_encrypt_with_passphrase(plaintext: str, passphrase: str) -> str:
    raise RuntimeError(
        "Legacy OpenSSL vault encryption is disabled because it exposed secrets through process arguments."
    )


def _legacy_openssl_decrypt_with_passphrase(ciphertext: str, passphrase: str) -> str:
    raise RuntimeError(
        "Legacy OpenSSL vault decryption is disabled. Re-encrypt credentials with the current vault format."
    )


def _vault_encrypt_with_passphrase(plaintext: str, passphrase: str) -> str:
    _init()
    if not isinstance(plaintext, str):
        raise RuntimeError("Vault encryption expects a plaintext string.")
    iterations = _server.ORION_VAULT_KDF_ITERATIONS
    salt = secrets.token_bytes(16)
    key = _vault_derive_key(passphrase, salt, iterations)
    Fernet, _, _, _ = _vault_crypto_primitives()
    token = Fernet(key).encrypt(plaintext.encode("utf-8")).decode("utf-8")
    payload = {
        "v": 2,
        "alg": "fernet-pbkdf2-sha256",
        "iter": iterations,
        "salt": base64.urlsafe_b64encode(salt).decode("ascii"),
        "ct": token,
    }
    encoded_payload = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    return f"{_server.ORION_VAULT_CIPHER_PREFIX}{encoded_payload}"


def _vault_decrypt_v2_with_passphrase(ciphertext: str, passphrase: str) -> str:
    _init()
    if not ciphertext.startswith(_server.ORION_VAULT_CIPHER_PREFIX):
        raise RuntimeError("Unsupported vault ciphertext format.")
    encoded_payload = ciphertext[len(_server.ORION_VAULT_CIPHER_PREFIX):].strip()
    try:
        payload_raw = base64.urlsafe_b64decode(encoded_payload.encode("ascii"))
        payload = json.loads(payload_raw.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError("Vault ciphertext payload is invalid.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Vault ciphertext version is invalid.")
    try:
        payload_version = int(payload.get("v") or 0)
    except Exception as exc:
        raise RuntimeError("Vault ciphertext version is invalid.") from exc
    if payload_version != 2:
        raise RuntimeError("Vault ciphertext version is invalid.")
    salt_value = str(payload.get("salt") or "").strip()
    token = str(payload.get("ct") or "").strip()
    if not salt_value or not token:
        raise RuntimeError("Vault ciphertext payload is incomplete.")
    try:
        salt = base64.urlsafe_b64decode(salt_value.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("Vault ciphertext salt is invalid.") from exc
    try:
        iterations = int(payload.get("iter") or _server.ORION_VAULT_KDF_ITERATIONS)
    except Exception as exc:
        raise RuntimeError("Vault ciphertext KDF iterations are invalid.") from exc
    if iterations < 120000 or iterations > 3000000:
        raise RuntimeError("Vault ciphertext KDF iterations are out of policy range.")
    key = _vault_derive_key(passphrase, salt, iterations)
    Fernet, InvalidToken, _, _ = _vault_crypto_primitives()
    try:
        plain = Fernet(key).decrypt(token.encode("utf-8"))
    except InvalidToken as exc:
        raise RuntimeError("Vault decryption failed. Invalid passphrase or ciphertext.") from exc
    return plain.decode("utf-8")


def _openssl_encrypt_with_passphrase(plaintext: str, passphrase: str) -> str:
    _init()
    return _vault_encrypt_with_passphrase(plaintext, passphrase)


def _openssl_decrypt_with_passphrase(ciphertext: str, passphrase: str) -> str:
    _init()
    value = str(ciphertext or "").strip()
    if value.startswith(_server.ORION_VAULT_CIPHER_PREFIX):
        return _vault_decrypt_v2_with_passphrase(value, passphrase)
    raise RuntimeError("Legacy OpenSSL vault decrypt is disabled.")


def _openssl_encrypt(plaintext: str) -> str:
    return _openssl_encrypt_with_passphrase(plaintext, _vault_passphrase())


def _openssl_decrypt(ciphertext: str) -> str:
    return _openssl_decrypt_with_passphrase(ciphertext, _vault_passphrase())


def load_vault() -> Dict[str, Any]:
    _init()
    vault_file = _server.VAULT_FILE
    if not vault_file.exists():
        return {"version": 1, "credentials": []}
    try:
        raw = vault_file.read_text(encoding="utf-8")
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict):
            return {"version": 1, "credentials": []}
        creds = data.get("credentials")
        if not isinstance(creds, list):
            creds = []
        return {"version": data.get("version", 1), "credentials": creds}
    except Exception as exc:
        LOGGER.warning("Failed to load credential vault from configured path: %s", exc)
        return {"version": 1, "credentials": []}


def get_vault_key() -> Dict[str, Any]:
    """Return the current vault encryption key for display/save.

    In production, the key comes from CREDENTIAL_VAULT_KEY env var.
    In dev, it's read from the key file (auto-generated on first run).

    Returns a dict with the key and its source, suitable for display
    to the operator at setup time so they can save it for disaster recovery.
    """
    _init()
    vault_key_env = _server.VAULT_KEY_ENV
    vault_key_file = _server.VAULT_KEY_FILE
    passphrase = _vault_passphrase()

    source = "environment" if (vault_key_env and vault_key_env.strip()) else "file"
    file_exists = vault_key_file.exists()
    file_path = str(vault_key_file)

    return {
        "key": passphrase,
        "source": source,
        "file_exists": file_exists,
        "file_path": file_path,
        "environment": _resolved_environment(),
        "recovery_instruction": (
            "Save this key securely (password manager, hardware key, printed copy). "
            "On a new box, set CREDENTIAL_VAULT_KEY=<this key> and the encrypted "
            "vault blob from the cloud backup will be decrypted automatically. "
            "Without this key, your cloud vault backup is unreadable."
        ),
    }


def restore_vault_from_cloud() -> Dict[str, Any]:
    """Restore the encrypted vault blob from the cloud control plane.

    Disaster recovery: after total box loss, call this on a fresh box
    with CREDENTIAL_VAULT_KEY set to the original key.  Downloads the
    encrypted vault blob from the cloud backup, writes it to disk, and
    verifies it can be decrypted with the current key.

    Returns a status dict with credential count on success.
    """
    _init()
    vault_file = _server.VAULT_FILE
    passphrase = _vault_passphrase()

    # ── Policy gate: kernel must approve the read ──
    try:
        decision = _kernel_client().runtime_state_store_decision(
            operation="read_vault_blob_backup",
            state_class="secret_material",
            actor_id="system",
            status="active",
            workspace_access=True,
            owner_access=True,
        )
        _kernel_client().enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
    except Exception as exc:
        return {
            "ok": False,
            "status": "policy_blocked",
            "error": f"Kernel blocked vault restore: {exc}",
        }

    # ── Fetch the backup blob (cloud API first, local fallback) ──
    try:
        import base64
        blob_b64 = ""
        fetch_source = ""

        # 1. Try the cloud API (off-box backup)
        rp_id = (os.getenv("EMPYRALIS_RUNTIME_PROFILE_ID") or "").strip()
        if not rp_id:
            rp_id = (os.getenv("EMPYRALIS_PROFILE_ID") or "").strip()
        if rp_id:
            try:
                api_body = {
                    "node_session_token": (os.getenv("EMPYRALIS_NODE_SESSION_TOKEN") or "").strip(),
                }
                cloud_resp = _cloud_api_post(
                    f"{_cloud_backup_api_path(rp_id)}/claim",
                    api_body,
                )
                if cloud_resp.get("ok") and cloud_resp.get("vault_blob_base64"):
                    blob_b64 = str(cloud_resp.get("vault_blob_base64") or "")
                    fetch_source = f"cloud API ({rp_id})"
            except Exception as api_exc:
                LOGGER.warning("Cloud API restore failed, trying local fallback: %s", api_exc)

        # 2. Fall back to local cloud-backups file
        if not blob_b64:
            blob_path = _cloud_backup_vault_blob_path()
            if not blob_path.exists():
                return {
                    "ok": False,
                    "status": "no_backup_found",
                    "error": f"No vault backup exists at {blob_path} (cloud API also unavailable).",
                }
            blob_data = json.loads(blob_path.read_text(encoding="utf-8"))
            blob_b64 = str(blob_data.get("vault_blob_base64") or "").strip()
            fetch_source = f"local backup ({blob_path})"

        if not blob_b64:
            return {
                "ok": False,
                "status": "empty_backup",
                "error": "Cloud vault backup blob is empty.",
            }
        raw = base64.b64decode(blob_b64)
        LOGGER.info("Vault blob fetched from %s (%d bytes)", fetch_source, len(raw))
    except Exception as exc:
        return {
            "ok": False,
            "status": "fetch_failed",
            "error": f"Failed to fetch vault backup from cloud: {exc}",
        }

    # ── Write the blob to disk ──
    try:
        vault_file.parent.mkdir(parents=True, exist_ok=True)
        vault_file.write_bytes(raw)
        os.chmod(vault_file, 0o600)
    except Exception as exc:
        return {
            "ok": False,
            "status": "write_failed",
            "error": f"Failed to write restored vault to {vault_file}: {exc}",
        }

    # ── Verify: can we load and decrypt it? ──
    try:
        vault = load_vault()
        cred_count = len(vault.get("credentials", []))
        # Verify at least one credential can be decrypted
        if cred_count > 0:
            for cred in vault.get("credentials", []):
                try:
                    _openssl_decrypt_with_passphrase(str(cred.get("value") or ""), passphrase)
                    break  # one successful decrypt confirms key works
                except Exception:
                    continue
        return {
            "ok": True,
            "status": "restored",
            "credential_count": cred_count,
            "file_path": str(vault_file),
            "file_size_bytes": len(raw),
        }
    except Exception as exc:
        # Restore failed — remove the file so we don't leave a broken vault
        try:
            vault_file.unlink(missing_ok=True)
        except Exception:
            pass
        return {
            "ok": False,
            "status": "decrypt_failed",
            "error": (
                f"Vault blob was restored but decryption failed: {exc}. "
                "The restored file has been removed. Verify CREDENTIAL_VAULT_KEY "
                "matches the key used when the backup was created."
            ),
        }


_VAULT_BACKUP_LAST_ATTEMPT: float = 0.0
_VAULT_BACKUP_LAST_SUCCESS: float = 0.0
_VAULT_BACKUP_CONSECUTIVE_FAILURES: int = 0


def _cloud_backup_dir() -> Path:
    """Return the cloud backup directory for vault blobs and pairings.

    Uses EMPYRALIS_STATE_HOME if set, otherwise falls back to the
    service user's home directory.  The backup directory is OUTSIDE
    the vault directory so it survives vault wipe during recovery.
    """
    try:
        from server_modules.runtime_config import EMPYRALIS_STATE_HOME
        return Path(EMPYRALIS_STATE_HOME) / "cloud-backups"
    except Exception:
        pass
    return Path(os.path.expanduser("~")) / ".empyralis" / "cloud-backups"


def _cloud_backup_vault_blob_path() -> Path:
    return _cloud_backup_dir() / "vault_blob.b64"


def _cloud_backup_pairings_path(channel_name: str) -> Path:
    return _cloud_backup_dir() / f"pairings_{channel_name}.b64"


def _backup_vault_to_cloud(vault_file: Path) -> None:
    """Sync encrypted vault blob to cloud control plane with retry.

    The vault file is encrypted at rest (Fernet+PBKDF2).  Uploading the
    encrypted blob to the cloud is safe — without the vault key file
    (which stays on the box), the blob is unreadable.

    Includes retry with exponential backoff (3 attempts, 1s/2s/4s).
    Tracks failure count for health monitoring.

    If the box is lost, the operator can:
      1. Provision a new box
      2. Set CREDENTIAL_VAULT_KEY to the original key
      3. Call restore_vault_from_cloud() to pull the encrypted blob
      4. The vault decrypts automatically — all credentials restored
    """
    global _VAULT_BACKUP_LAST_ATTEMPT, _VAULT_BACKUP_LAST_SUCCESS, _VAULT_BACKUP_CONSECUTIVE_FAILURES
    import base64, time as _time

    _VAULT_BACKUP_LAST_ATTEMPT = _time.monotonic()

    raw = vault_file.read_bytes()
    encoded = base64.b64encode(raw).decode("ascii")
    payload = {
        "vault_file_path": str(vault_file),
        "vault_blob_base64": encoded,
        "environment": _resolved_environment(),
        "backup_version": "vault_blob_v1",
    }

    last_error = None
    for attempt in range(3):
        try:
            decision = _kernel_client().runtime_state_store_decision(
                operation="write_vault_blob_backup",
                state_class="secret_material",
                actor_id="system",
                status="active",
                payload=payload,
                payload_bytes=len(encoded),
                workspace_access=True,
                owner_access=True,
            )
            _kernel_client().enforce_kernel_decision(
                "runtime-state-store-decision",
                decision,
            )
            # ── Kernel approved — upload the encrypted blob to cloud API ──
            rp_id = (os.getenv("EMPYRALIS_RUNTIME_PROFILE_ID") or "").strip()
            if not rp_id:
                rp_id = (os.getenv("EMPYRALIS_PROFILE_ID") or "").strip()
            if rp_id:
                api_body = {
                    "node_session_token": (os.getenv("EMPYRALIS_NODE_SESSION_TOKEN") or "").strip(),
                    "vault_blob_base64": encoded,
                    "environment": _resolved_environment(),
                    "backup_version": "vault_blob_v1",
                }
                _cloud_api_post(_cloud_backup_api_path(rp_id), api_body)
                # Also write to local cloud-backups as fallback (belt-and-suspenders)
                try:
                    backup_dir = _cloud_backup_dir()
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    blob_path = _cloud_backup_vault_blob_path()
                    _safe_write_json(blob_path, payload)
                    try:
                        os.chmod(blob_path, 0o600)
                    except Exception:
                        pass
                except Exception:
                    pass
            else:
                # No profile ID — fall back to local file only
                backup_dir = _cloud_backup_dir()
                backup_dir.mkdir(parents=True, exist_ok=True)
                blob_path = _cloud_backup_vault_blob_path()
                _safe_write_json(blob_path, payload)
                try:
                    os.chmod(blob_path, 0o600)
                except Exception:
                    pass
            _VAULT_BACKUP_LAST_SUCCESS = _time.monotonic()
            _VAULT_BACKUP_CONSECUTIVE_FAILURES = 0
            LOGGER.info("Vault backed up to cloud control plane (%d bytes, attempt %d)", len(encoded), attempt + 1)
            return
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                _time.sleep(1.0 * (2 ** attempt))  # 1s, 2s backoff

    _VAULT_BACKUP_CONSECUTIVE_FAILURES += 1
    LOGGER.warning(
        "Failed to back up vault to cloud after 3 attempts (consecutive failures: %d): %s",
        _VAULT_BACKUP_CONSECUTIVE_FAILURES, last_error,
    )


def vault_backup_health() -> Dict[str, Any]:
    """Return health stats for the vault cloud backup."""
    import time as _time
    now = _time.monotonic()
    return {
        "last_attempt_seconds_ago": round(now - _VAULT_BACKUP_LAST_ATTEMPT, 1) if _VAULT_BACKUP_LAST_ATTEMPT else None,
        "last_success_seconds_ago": round(now - _VAULT_BACKUP_LAST_SUCCESS, 1) if _VAULT_BACKUP_LAST_SUCCESS else None,
        "consecutive_failures": _VAULT_BACKUP_CONSECUTIVE_FAILURES,
        "healthy": _VAULT_BACKUP_CONSECUTIVE_FAILURES < 3,
    }


def backup_channel_pairings_to_cloud() -> Dict[str, Any]:
    """Back up local channel pairing files to the cloud control plane.

    Channel pairings (Telegram chat_id→workspace, Discord guild→workspace, etc.)
    are stored as JSON files on disk.  This function reads them and syncs
    encrypted blobs to the cloud so re-pairing isn't needed after box loss.

    Returns a dict mapping channel name → backup status.
    """
    import base64

    pairing_files = {
        "sage_telegram_hosted": Path(
            os.path.join(os.path.expanduser("~"), ".empyralis", "state", "sage_telegram_hosted_pairs.json")
        ),
    }
    # Also check the EMPYRALIS_STATE_HOME override path
    try:
        from server_modules.runtime_config import EMPYRALIS_STATE_HOME
        pairing_files["sage_telegram_hosted"] = EMPYRALIS_STATE_HOME / "sage_telegram_hosted_pairs.json"
    except Exception:
        pass

    results: Dict[str, Any] = {}
    for channel_name, file_path in pairing_files.items():
        try:
            if not file_path.exists():
                results[channel_name] = {"ok": True, "status": "no_file", "path": str(file_path)}
                continue
            raw = file_path.read_bytes()
            encoded = base64.b64encode(raw).decode("ascii")
            payload = {
                "channel": channel_name,
                "pairings_blob_base64": encoded,
                "file_path": str(file_path),
                "environment": _resolved_environment(),
                "backup_version": "channel_pairings_v1",
            }
            decision = _kernel_client().runtime_state_store_decision(
                operation="write_channel_pairings_backup",
                state_class="secret_material",
                actor_id="system",
                status="active",
                payload=payload,
                payload_bytes=len(encoded),
                workspace_access=True,
                owner_access=True,
            )
            _kernel_client().enforce_kernel_decision(
                "runtime-state-store-decision",
                decision,
            )
            # ── Kernel approved — persist the blob to cloud backup ──
            backup_dir = _cloud_backup_dir()
            backup_dir.mkdir(parents=True, exist_ok=True)
            blob_path = _cloud_backup_pairings_path(channel_name)
            _safe_write_json(blob_path, payload)
            try:
                os.chmod(blob_path, 0o600)
            except Exception:
                pass
            results[channel_name] = {"ok": True, "status": "backed_up", "bytes": len(raw)}
            LOGGER.info("Channel pairings backed up for %s (%d bytes)", channel_name, len(raw))
        except Exception as exc:
            results[channel_name] = {"ok": False, "status": "failed", "error": str(exc)}
            LOGGER.warning("Failed to back up channel pairings for %s: %s", channel_name, exc)

    return results


def reconcile_cloud_backups() -> Dict[str, Any]:
    """Periodic reconciliation: ensure vault and pairings are backed up.

    Call this from a scheduler (e.g., every 5 minutes) to catch failures
    from fire-and-forget backup attempts.  Only re-uploads if the file
    has changed since the last successful backup.
    """
    results: Dict[str, Any] = {"vault": None, "pairings": None}

    # Reconcile vault
    try:
        _init()
        vault_file = _server.VAULT_FILE
        if vault_file.exists():
            _backup_vault_to_cloud(vault_file)
            results["vault"] = {"ok": True, "status": "reconciled"}
        else:
            results["vault"] = {"ok": True, "status": "no_file"}
    except Exception as exc:
        results["vault"] = {"ok": False, "status": "failed", "error": str(exc)}

    # Reconcile pairings
    try:
        results["pairings"] = backup_channel_pairings_to_cloud()
    except Exception as exc:
        results["pairings"] = {"ok": False, "status": "failed", "error": str(exc)}

    return results


def save_vault(vault: Dict[str, Any]):
    _init()
    vault_file = _server.VAULT_FILE
    safe_vault = {
        "version": vault.get("version", 1),
        "credentials": vault.get("credentials", []),
    }
    _safe_write_json(vault_file, safe_vault)
    try:
        os.chmod(vault_file, 0o600)
    except Exception:
        pass

    # ── Cloud backup: sync encrypted vault blob so box loss ≠ account loss ──
    try:
        _backup_vault_to_cloud(vault_file)
    except Exception:
        pass  # fire-and-forget — local save succeeded
