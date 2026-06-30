"""OAuth token exchange — authorization_code → tokens.

Port of v1's _exchange_google, _exchange_slack, _exchange_notion patterns,
simplified: no httpx dependency for exchange (stdlib urllib is enough for one-off)."""

import base64
import json
import os
import time
import urllib.request
from typing import Any

from server.oauth.provider_configs import PROVIDERS, get_provider


def _post_form(url: str, data: dict[str, str], headers: dict[str, str] | None = None) -> dict[str, Any]:
    """POST x-www-form-urlencoded, return JSON response."""
    body = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in data.items()).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _post_json(url: str, data: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    """POST JSON, return JSON response."""
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _get_client_creds(provider_key: str) -> tuple[str, str]:
    config = get_provider(provider_key)
    client_id = os.getenv(config.client_id_env, "").strip()
    client_secret = os.getenv(config.client_secret_env, "").strip()
    if not client_id or not client_secret:
        raise RuntimeError(
            f"OAuth not configured for {provider_key}. "
            f"Set {config.client_id_env} and {config.client_secret_env}."
        )
    return client_id, client_secret


def exchange_google(code: str, redirect_uri: str) -> dict[str, Any]:
    config = get_provider("google_workspace")
    client_id, client_secret = _get_client_creds("google_workspace")
    payload = _post_form(config.token_url, {
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    })
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError(str(payload.get("error_description") or payload.get("error") or "Google token exchange failed."))
    expires_in = int(payload.get("expires_in") or 0)
    creds: dict[str, Any] = {
        "auth_mode": "oauth", "provider": "google_workspace",
        "access_token": access_token,
        "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer",
        "scope": str(payload.get("scope") or "").strip(),
    }
    refresh_token = str(payload.get("refresh_token") or "").strip()
    if refresh_token:
        creds["refresh_token"] = refresh_token
    if expires_in > 0:
        creds["access_token_expires_at"] = int(time.time()) + expires_in
    return creds


def exchange_slack(code: str, redirect_uri: str) -> dict[str, Any]:
    config = get_provider("slack")
    client_id, client_secret = _get_client_creds("slack")
    payload = _post_form(config.token_url, {
        "code": code, "client_id": client_id, "client_secret": client_secret,
        "redirect_uri": redirect_uri,
    })
    if not payload.get("ok"):
        raise RuntimeError(f"Slack OAuth failed: {payload.get('error', 'unknown')}")
    bot_token = str((payload.get("bot_user_id") and {}).get("access_token")
                    or payload.get("access_token") or "").strip()
    # Slack v2 OAuth returns nested structure
    if not bot_token and isinstance(payload.get("authed_user"), dict):
        bot_token = str(payload["authed_user"].get("access_token") or "").strip()
    if not bot_token and isinstance(payload.get("bot"), dict):
        bot_token = str(payload["bot"].get("access_token") or "").strip()
    if not bot_token:
        # Fallback: try the root access_token
        bot_token = str(payload.get("access_token") or "").strip()
    if not bot_token:
        raise RuntimeError("Slack OAuth did not return a bot token.")
    return {"auth_mode": "oauth", "provider": "slack", "bot_token": bot_token,
            "scope": str(payload.get("scope") or "").strip()}


def exchange_notion(code: str, redirect_uri: str) -> dict[str, Any]:
    client_id, client_secret = _get_client_creds("notion")
    auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    payload = _post_json(
        get_provider("notion").token_url,
        {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri},
        headers={"Authorization": f"Basic {auth}"},
    )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise RuntimeError("Notion token exchange failed.")
    return {"auth_mode": "oauth", "provider": "notion", "access_token": access_token,
            "token_type": str(payload.get("token_type") or "Bearer").strip() or "Bearer"}


def exchange_code(provider_key: str, code: str, redirect_uri: str) -> dict[str, Any]:
    provider = provider_key.strip().lower()
    if provider == "google_workspace":
        return exchange_google(code, redirect_uri)
    if provider == "slack":
        return exchange_slack(code, redirect_uri)
    if provider == "notion":
        return exchange_notion(code, redirect_uri)
    raise ValueError(f"Token exchange not implemented for: {provider_key}")
