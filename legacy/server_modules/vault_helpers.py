from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from server_modules import secret_redaction_service

LoadVaultFn = Callable[[], Dict[str, Any]]
DecryptFn = Callable[[str], str]
SafeReadJsonFn = Callable[[Path, Dict[str, Any]], Dict[str, Any]]


def normalize_workspace_id(workspace_id: Optional[str]) -> Optional[str]:
    if not workspace_id:
        return None
    value = str(workspace_id).strip()
    return value or None


def workspace_visible(entry_workspace_id: Optional[str], requested_workspace_id: Optional[str]) -> bool:
    entry_ws = normalize_workspace_id(entry_workspace_id)
    req_ws = normalize_workspace_id(requested_workspace_id)
    if req_ws is None:
        return True
    return entry_ws == req_ws


def list_vault_credentials(
    load_vault_fn: LoadVaultFn,
    connector_catalog: Dict[str, Any],
    workspace_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in load_vault_fn().get("credentials", []):
        if not workspace_visible(entry.get("workspace_id"), workspace_id):
            continue
        provider = entry.get("provider")
        if isinstance(provider, str) and provider in connector_catalog:
            continue
        out.append(
            {
                "id": entry.get("id"),
                "label": entry.get("label"),
                "provider": entry.get("provider"),
                "mode": entry.get("mode", "byok"),
                "workspace_id": entry.get("workspace_id"),
                "metadata": secret_redaction_service.sanitize_mapping(entry.get("metadata")),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
        )
    return out


def list_vault_connectors(
    load_vault_fn: LoadVaultFn,
    connector_catalog: Dict[str, Any],
    workspace_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for entry in load_vault_fn().get("credentials", []):
        if not workspace_visible(entry.get("workspace_id"), workspace_id):
            continue
        provider = entry.get("provider")
        if not isinstance(provider, str) or provider not in connector_catalog:
            continue
        out.append(
            {
                "id": entry.get("id"),
                "label": entry.get("label"),
                "connector": provider,
                "workspace_id": entry.get("workspace_id"),
                "metadata": secret_redaction_service.sanitize_mapping(entry.get("metadata")),
                "created_at": entry.get("created_at"),
                "updated_at": entry.get("updated_at"),
            }
        )
    return out


_OAUTH_REFRESH_IN_FLIGHT: set = set()


def resolve_vault_credential(
    load_vault_fn: LoadVaultFn,
    decrypt_fn: DecryptFn,
    credential_id: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Auto-refresh OAuth tokens before reading from vault.
    # Guard against re-entry — refresh_oauth_token_if_needed calls this
    # function internally, and we don't want to recurse.
    normalized_id = str(credential_id or "").strip()
    if normalized_id and normalized_id not in _OAUTH_REFRESH_IN_FLIGHT:
        _OAUTH_REFRESH_IN_FLIGHT.add(normalized_id)
        try:
            from server_modules.connection_oauth_service import refresh_oauth_token_if_needed
            refresh_oauth_token_if_needed(normalized_id)
        except Exception:
            pass
        finally:
            _OAUTH_REFRESH_IN_FLIGHT.discard(normalized_id)

    vault = load_vault_fn()
    for entry in vault.get("credentials", []):
        if entry.get("id") != credential_id:
            continue
        if not workspace_visible(entry.get("workspace_id"), workspace_id):
            raise RuntimeError("Credential is not accessible for this workspace.")
        encrypted = entry.get("encrypted_secret")
        if not isinstance(encrypted, str) or not encrypted:
            raise RuntimeError("Credential payload missing.")
        plain = decrypt_fn(encrypted)
        payload = __import__("json").loads(plain)
        if not isinstance(payload, dict):
            raise RuntimeError("Credential payload is invalid.")
        payload["_provider"] = entry.get("provider")
        payload["_label"] = entry.get("label")
        payload["_mode"] = entry.get("mode", "byok")
        payload["_workspace_id"] = entry.get("workspace_id")
        payload["_metadata"] = entry.get("metadata", {}) if isinstance(entry.get("metadata"), dict) else {}
        return payload
    raise RuntimeError("Credential ID not found.")


def parse_iso_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=timezone.utc)
    raw = value.strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except Exception:
        return datetime.min.replace(tzinfo=timezone.utc)


def resolve_default_vault_credential(
    load_vault_fn: LoadVaultFn,
    decrypt_fn: DecryptFn,
    provider: str,
    workspace_id: Optional[str] = None,
) -> Dict[str, Any]:
    provider_id = str(provider or "").strip().lower()
    if not provider_id:
        raise RuntimeError("Provider is required.")

    requested_ws = normalize_workspace_id(workspace_id)
    candidates: List[Dict[str, Any]] = []
    for entry in load_vault_fn().get("credentials", []):
        if str(entry.get("provider") or "").strip().lower() != provider_id:
            continue
        if not workspace_visible(entry.get("workspace_id"), requested_ws):
            continue
        candidates.append(entry)

    if not candidates:
        raise RuntimeError(f"No credential available for provider '{provider_id}'.")

    def _candidate_key(item: Dict[str, Any]) -> Tuple[int, datetime]:
        item_ws = normalize_workspace_id(item.get("workspace_id"))
        exact_match = bool(requested_ws and item_ws == requested_ws)
        ts = parse_iso_datetime(item.get("updated_at") or item.get("created_at"))
        return (1 if exact_match else 0, ts)

    best = sorted(candidates, key=_candidate_key, reverse=True)[0]
    credential_id = str(best.get("id") or "").strip()
    if not credential_id:
        raise RuntimeError(f"Credential entry for provider '{provider_id}' is missing an id.")
    return resolve_vault_credential(load_vault_fn, decrypt_fn, credential_id, requested_ws)


def credential_identity(provider: str, label: str, workspace_id: Optional[str]) -> str:
    ws = normalize_workspace_id(workspace_id) or "__global__"
    return f"{provider.strip().lower()}::{label.strip().lower()}::{ws}"


def sanitize_bearer_token(value: Any) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    return token


def codex_token_from_vault(codex_auth_file: Path, safe_read_json_fn: SafeReadJsonFn) -> str:
    try:
        if not codex_auth_file.exists():
            return ""
        payload = safe_read_json_fn(codex_auth_file, {})
        if not isinstance(payload, dict):
            return ""
        tokens = payload.get("tokens")
        if not isinstance(tokens, dict):
            return ""
        return sanitize_bearer_token(tokens.get("access_token"))
    except Exception:
        return ""


def openai_env_bearer_with_source(
    *,
    codex_oauth_token: str,
    openai_oauth_token: str,
    openai_access_token: str,
    codex_vault_token: str,
    disable_openai_api_key: bool,
    auth_mode: str,
) -> Tuple[str, str]:
    codex_candidates = [
        ("env_codex_oauth_token", codex_oauth_token),
        ("env_oauth_token", openai_oauth_token),
        ("env_access_token", openai_access_token),
        ("codex_token_vault", codex_vault_token),
    ]
    api_key_candidates: List[Tuple[str, Any]] = []
    if not disable_openai_api_key:
        api_key_candidates.append(("env_api_key", os.getenv("OPENAI_API_KEY")))

    if auth_mode == "api_key":
        candidates = api_key_candidates + codex_candidates
    else:
        candidates = codex_candidates + api_key_candidates
    for source, raw in candidates:
        token = sanitize_bearer_token(raw)
        if token:
            return token, source
    return "", "none"


def openai_bearer_from_credentials(credentials: Dict[str, Any]) -> str:
    for key in ("api_key", "access_token", "oauth_token", "token"):
        token = sanitize_bearer_token(credentials.get(key))
        if token:
            return token
    return ""
