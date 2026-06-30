"""Systemic OAuth refresh — auto-refresh on every credential read.

Port of v1's refresh_oauth_token_if_needed pattern (MAN-14 systemic fix):
every credential read through resolve_credential() auto-refreshes if
the token expires within 5 minutes."""

import json
import logging
import os
import time
from typing import Any

from server.oauth.provider_configs import PROVIDERS, get_provider
from server.vault.store import get_credential, set_credential

_log = logging.getLogger(__name__)

_REFRESH_WINDOW = 300  # 5 minutes


def resolve_credential(vault: dict[str, Any], credential_id: str) -> dict[str, Any] | None:
    """Read a credential from vault with automatic OAuth refresh.

    This is the systemic fix from v1 MAN-14: every credential read
    goes through this function, which checks expiration and refreshes
    proactively. No more scattered refresh calls at every MCP call site."""
    cred = get_credential(vault, credential_id)
    if not isinstance(cred, dict) or not cred:
        return cred

    # Only refresh OAuth credentials with an expiration
    expires_at = cred.get("access_token_expires_at") or cred.get("expires_at") or 0
    expires_at = int(expires_at or 0)
    if expires_at <= 0:
        return cred

    now = int(time.time())
    if expires_at > now + _REFRESH_WINDOW:
        return cred  # still fresh

    refresh_token = str(cred.get("refresh_token") or "").strip()
    if not refresh_token:
        return cred  # can't refresh without a refresh_token

    provider_key = str(cred.get("provider") or "").strip().lower()
    if not provider_key or provider_key not in PROVIDERS:
        return cred

    try:
        config = get_provider(provider_key)
        client_id = os.getenv(config.client_id_env, "").strip()
        client_secret = os.getenv(config.client_secret_env, "").strip()
        if not client_id or not client_secret:
            _log.warning("OAuth refresh: provider %s not configured", provider_key)
            return cred

        # Call the provider's token refresh endpoint
        import urllib.request
        body = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in {
            "grant_type": "refresh_token", "refresh_token": refresh_token,
            "client_id": client_id, "client_secret": client_secret,
        }.items()).encode()
        req = urllib.request.Request(config.token_url, data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=30) as resp:
            token_resp = json.loads(resp.read())

        new_access_token = str(token_resp.get("access_token") or "").strip()
        if not new_access_token:
            _log.warning("OAuth refresh: no access_token in response for %s", provider_key)
            return cred

        cred["access_token"] = new_access_token
        new_expires_in = int(token_resp.get("expires_in") or 0)
        if new_expires_in > 0:
            cred["access_token_expires_at"] = int(time.time()) + new_expires_in
        new_refresh = str(token_resp.get("refresh_token") or "").strip()
        if new_refresh:
            cred["refresh_token"] = new_refresh

        # Persist back to vault
        set_credential(vault, credential_id, cred)
        _log.info("OAuth refresh: refreshed token for %s/%s", provider_key, credential_id)
    except Exception as exc:
        _log.warning("OAuth refresh failed for %s/%s: %s", provider_key, credential_id, exc)

    return cred
