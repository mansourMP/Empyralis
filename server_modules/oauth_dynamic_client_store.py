"""Durable storage for RFC 7591 dynamically-registered OAuth clients.

Why this module exists (MAN-124)
--------------------------------
`connection_oauth_service._DYNAMIC_CLIENT_CACHE` used to be the ONLY place a
dynamically-registered client_id/client_secret lived. That is a plain
in-process dict, and the OAuth flow spans two independent HTTP requests:

  1. `start_oauth()` registers a client with the provider and sends the user
     to the provider's consent screen with that client_id.
  2. The provider redirects back to `/callback`, which must exchange the auth
     code using the SAME client_id/secret.

If the process restarted between (1) and (2) — a deploy, a crash, a scale-to-
zero — or if the callback landed on a different worker/pod, step (2) found an
empty dict and registered a *different* client. The auth code was issued to
the first client, so the exchange failed with `invalid_grant` /
`invalid_client`. Worse, the background refresh path had no redirect_uri and
so could not re-register at all: every DCR-based connection died permanently
after a restart and the user had to reconnect from scratch.

Storage model
-------------
A registration is stored as one row in the existing encrypted credential
vault (`vault_credentials`), so it inherits the established convention:
Fernet+PBKDF2 encryption at rest (`vault_store._openssl_encrypt`), Postgres
single-row writes, vault-key rotation, and cloud backup. No new table and no
new plaintext store.

  * `id`        — deterministic: `dcr::{provider}::{sha256(redirect_uri)}`,
                  so an upsert from any worker converges on the same row.
  * `provider`  — the sentinel `_oauth_dcr_client`, NOT the real connector id.
                  This keeps these platform-owned rows out of every resolver
                  that looks credentials up by provider name
                  (`resolve_default_vault_credential`) and out of the user's
                  credential lists (see `vault_helpers.INTERNAL_VAULT_
                  PROVIDERS`). The real provider lives in metadata.
  * secret      — the encrypted JSON blob holds `client_secret` (and a copy of
                  the other fields). `client_secret` NEVER lands in metadata.
  * metadata    — non-secret bookkeeping only: provider, redirect_uri,
                  client_id, and the two RFC 7591 timing fields. `redirect_uri`
                  in particular is what lets the background refresh path
                  re-register a client whose secret expired (Linear's expires
                  every 24h) without a live HTTP request to derive it from.

Every function here raises on failure; callers in connection_oauth_service
treat a storage failure as "not persisted" and fall back to the in-process
cache, so a vault/Postgres outage degrades to today's behaviour rather than
breaking OAuth outright.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("empyralis.oauth_dcr_store")

# Sentinel provider for these platform-owned rows. Deliberately not a real
# connector id — see the module docstring.
INTERNAL_DCR_PROVIDER = "_oauth_dcr_client"

_METADATA_KIND = "oauth_dynamic_client_registration"


def credential_id(provider: str, redirect_uri: str) -> str:
    """Deterministic vault row id for a (provider, redirect_uri) registration.

    Deterministic so two workers racing the same registration upsert the same
    row instead of accumulating duplicates. The redirect_uri is hashed rather
    than embedded: it is a URL (unbounded, contains `/` and `:`) and only its
    identity matters here — the readable copy lives in metadata.
    """
    normalized_provider = str(provider or "").strip().lower()
    normalized_redirect = str(redirect_uri or "").strip()
    digest = hashlib.sha256(normalized_redirect.encode("utf-8")).hexdigest()
    return f"dcr::{normalized_provider}::{digest}"


def _vault():
    from server_modules import vault_store

    return vault_store


def _record_from_entry(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Decrypt one vault row into a registration record, or None if the row is
    not a DCR registration / cannot be decrypted."""
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    if str(metadata.get("kind") or "") != _METADATA_KIND:
        return None
    encrypted = entry.get("encrypted_secret")
    if not isinstance(encrypted, str) or not encrypted:
        return None
    payload = json.loads(_vault()._openssl_decrypt(encrypted))
    if not isinstance(payload, dict):
        return None
    client_id = str(payload.get("client_id") or "").strip()
    if not client_id:
        return None
    return {
        "provider": str(metadata.get("provider") or payload.get("provider") or "").strip().lower(),
        "redirect_uri": str(metadata.get("redirect_uri") or payload.get("redirect_uri") or "").strip(),
        "client_id": client_id,
        "client_secret": str(payload.get("client_secret") or ""),
        "client_id_issued_at": int(payload.get("client_id_issued_at") or 0),
        "client_secret_expires_at": int(payload.get("client_secret_expires_at") or 0),
        "updated_at": entry.get("updated_at") or entry.get("created_at") or "",
    }


def load(provider: str, redirect_uri: str) -> Optional[Dict[str, Any]]:
    """Read the registration for an exact (provider, redirect_uri) pair.

    Returns None when nothing is stored. Expiry is NOT evaluated here — the
    caller decides, because an expired record is still useful: its
    redirect_uri is what makes re-registration possible.
    """
    entry = _vault().get_credential(credential_id(provider, redirect_uri))
    if not isinstance(entry, dict):
        return None
    return _record_from_entry(entry)


def load_all_for_provider(provider: str) -> List[Dict[str, Any]]:
    """Every stored registration for a provider, newest-updated first.

    Used by the background refresh path, which has no redirect_uri of its own
    and so cannot compute a row id. In practice a deployment has one origin
    and therefore one row per provider; more than one only appears if the
    public origin changed.
    """
    normalized = str(provider or "").strip().lower()
    records: List[Dict[str, Any]] = []
    for entry in _vault().list_credentials():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("provider") or "") != INTERNAL_DCR_PROVIDER:
            continue
        try:
            record = _record_from_entry(entry)
        except Exception as exc:  # one unreadable row must not hide the others
            _log.warning("Skipping unreadable DCR registration row %s: %s", entry.get("id"), exc)
            continue
        if record is None or record.get("provider") != normalized:
            continue
        records.append(record)
    records.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return records


def save(
    *,
    provider: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    client_id_issued_at: int = 0,
    client_secret_expires_at: int = 0,
) -> None:
    """Persist (encrypted) one registration, replacing any prior row for the
    same (provider, redirect_uri)."""
    normalized_provider = str(provider or "").strip().lower()
    normalized_redirect = str(redirect_uri or "").strip()
    secret_payload = {
        "provider": normalized_provider,
        "redirect_uri": normalized_redirect,
        "client_id": str(client_id or "").strip(),
        "client_secret": str(client_secret or ""),
        "client_id_issued_at": int(client_id_issued_at or 0),
        "client_secret_expires_at": int(client_secret_expires_at or 0),
    }
    _vault().upsert_credential(
        {
            "id": credential_id(normalized_provider, normalized_redirect),
            "provider": INTERNAL_DCR_PROVIDER,
            "workspace_id": None,
            "platform_scoped": True,
            "label": f"OAuth dynamic client ({normalized_provider})",
            "mode": "platform",
            "metadata": {
                # NOTE: client_secret is deliberately absent — metadata is
                # stored as plaintext JSONB.
                "kind": _METADATA_KIND,
                "provider": normalized_provider,
                "redirect_uri": normalized_redirect,
                "client_id": secret_payload["client_id"],
                "client_id_issued_at": secret_payload["client_id_issued_at"],
                "client_secret_expires_at": secret_payload["client_secret_expires_at"],
            },
            "encrypted_secret": _vault()._openssl_encrypt(
                json.dumps(secret_payload, separators=(",", ":"))
            ),
        }
    )


def delete(provider: str, redirect_uri: str) -> bool:
    return bool(_vault().delete_credential(credential_id(provider, redirect_uri)))
