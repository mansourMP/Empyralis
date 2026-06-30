from __future__ import annotations

import os
import secrets
import threading
from pathlib import Path

from server_modules import rust_runtime_kernel_client


EMPYRALIS_STATE_HOME = Path(
    os.getenv("EMPYRALIS_STATE_HOME", str(Path.home() / ".empyralis" / "state"))
).expanduser()
JWT_SECRET_FILE = Path(
    os.getenv("EMPYRALIS_JWT_SECRET_FILE", str(EMPYRALIS_STATE_HOME / "auth" / "jwt_secret"))
).expanduser()

_JWT_SECRET_LOCK = threading.Lock()
_JWT_SECRET_CACHE: str | None = None
_LOCAL_ENV_TOKENS = {"", "dev", "development", "local", "test", "testing"}
_PLACEHOLDER_SECRET_TOKENS = {
    "replace-with-a-random-32-plus-character-secret",
    "change-me",
    "changeme",
    "secret",
    "test-secret",
}


def _normalize_secret(raw: str | None) -> str:
    return str(raw or "").strip()


def _explicit_secret() -> str:
    for key in ("ORION_JWT_SECRET", "JWT_SECRET"):
        secret = _normalize_secret(os.getenv(key))
        if secret:
            return secret
    return ""


def explicit_secret_is_safe_for_environment(environment: str | None = None) -> bool:
    env = str(environment or os.getenv("ORION_ENV") or os.getenv("ENV") or "").strip().lower()
    if env in _LOCAL_ENV_TOKENS:
        return True
    explicit = _explicit_secret()
    if len(explicit) < 32:
        return False
    if explicit.strip().lower() in _PLACEHOLDER_SECRET_TOKENS:
        return False
    return True


def assert_explicit_secret_safe_for_environment(environment: str | None = None) -> None:
    if not explicit_secret_is_safe_for_environment(environment):
        raise RuntimeError(
            "ORION_JWT_SECRET or JWT_SECRET must be explicitly set to a non-placeholder "
            "32+ character secret in staging/production."
        )


def _read_secret_file(path: Path) -> str:
    try:
        return _normalize_secret(path.read_text(encoding="utf-8"))
    except Exception:
        return ""


class JwtSecretRustGateError(RuntimeError):
    pass


def _enforce_jwt_secret_state_decision(*, path: Path, secret: str) -> None:
    payload = {
        "path": str(path),
        "secret_length": len(_normalize_secret(secret)),
    }
    try:
        decision = rust_runtime_kernel_client.runtime_state_store_decision(
            operation="write_jwt_secret_file",
            state_class="secret_material",
            actor_id="system",
            status="active",
            payload=payload,
            payload_bytes=len(str(payload).encode("utf-8")),
            workspace_access=True,
            owner_access=True,
        )
        rust_runtime_kernel_client.enforce_kernel_decision(
            "runtime-state-store-decision",
            decision,
        )
        next_action = str(decision.get("next_action") or "").strip()
        if next_action != "write_jwt_secret_file":
            raise JwtSecretRustGateError("unexpected_next_action")
    except rust_runtime_kernel_client.RustKernelDecisionError as exc:
        raise JwtSecretRustGateError(exc.reason) from exc


def _write_secret_file(path: Path, secret: str) -> str:
    normalized = _normalize_secret(secret)
    if not normalized:
        raise RuntimeError("JWT secret is empty.")
    _enforce_jwt_secret_state_decision(path=path, secret=normalized)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{normalized}\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return normalized


def resolve_jwt_secret() -> str:
    global _JWT_SECRET_CACHE

    explicit = _explicit_secret()
    if explicit:
        _JWT_SECRET_CACHE = explicit
        return explicit

    with _JWT_SECRET_LOCK:
        if _JWT_SECRET_CACHE:
            return _JWT_SECRET_CACHE

        persisted = _read_secret_file(JWT_SECRET_FILE)
        if persisted:
            _JWT_SECRET_CACHE = persisted
            return persisted

        generated = secrets.token_urlsafe(48)
        _JWT_SECRET_CACHE = _write_secret_file(JWT_SECRET_FILE, generated)
        return _JWT_SECRET_CACHE
