from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib import request as urlrequest

from fastapi import HTTPException

from server_modules import runtime_common
from server_modules.connectors import discord_connector


VERIFY_SUPPORTED_CONNECTION_IDS = {"telegram_bot", "discord_bot"}


@dataclass(frozen=True)
class ConnectionVerifyResult:
    valid: bool
    account_name: str
    account_id: Optional[str] = None


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).lower()


def _latest_credential_id(*, workspace_id: str, provider: str, connection_id: str) -> str:
    normalized_provider = _token(provider)
    normalized_connection_id = _token(connection_id)
    rows = runtime_common.list_vault_credentials(workspace_id=workspace_id)
    candidates: list[Dict[str, Any]] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        row_provider = _token(row.get("provider") or row.get("connector"))
        if row_provider != normalized_provider:
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        metadata_connection_id = _token(metadata.get("connection_id"))
        if metadata_connection_id and metadata_connection_id != normalized_connection_id:
            continue
        if metadata.get("channel_account") is False:
            continue
        candidates.append(row)
    if not candidates:
        raise HTTPException(status_code=409, detail="Save this connection before verifying it.")
    candidates.sort(key=lambda item: _text(item.get("updated_at") or item.get("created_at") or item.get("id")))
    credential_id = _text(candidates[-1].get("id"))
    if not credential_id:
        raise HTTPException(status_code=409, detail="Saved connection credential is missing an id.")
    return credential_id


def _credential_secret(*, workspace_id: str, provider: str, connection_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
    credential_id = _latest_credential_id(
        workspace_id=workspace_id,
        provider=provider,
        connection_id=connection_id,
    )
    secret = runtime_common.resolve_vault_credential(
        credential_id,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        connector_id=provider,
        purpose="connection_verify",
        allowed_fields=None,
    )
    if not isinstance(secret, dict):
        raise HTTPException(status_code=409, detail="Saved connection credential could not be resolved.")
    return secret


def _telegram_get_me(bot_token: str) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/getMe"
    req = urlrequest.Request(url, method="GET")
    try:
        with urlrequest.urlopen(req, timeout=15) as resp:
            raw = resp.read()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Telegram bot token could not be verified.") from exc
    try:
        body = json.loads(raw.decode("utf-8", errors="replace"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Telegram verification returned an invalid response.") from exc
    if not isinstance(body, dict) or body.get("ok") is not True:
        raise HTTPException(status_code=400, detail="Telegram bot token could not be verified.")
    result = body.get("result")
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail="Telegram verification returned no bot identity.")
    return result


def _verify_telegram_bot(credentials: Dict[str, Any]) -> ConnectionVerifyResult:
    bot_token = _text(credentials.get("bot_token") or credentials.get("token"))
    if not bot_token:
        raise HTTPException(status_code=400, detail="Telegram bot token is required.")
    result = _telegram_get_me(bot_token)
    account_id = _text(result.get("id")) or None
    username = _text(result.get("username"))
    first_name = _text(result.get("first_name"))
    account_name = f"@{username}" if username else first_name or (f"Telegram bot {account_id}" if account_id else "Telegram bot")
    return ConnectionVerifyResult(valid=True, account_name=account_name, account_id=account_id)


def _verify_discord_bot(credentials: Dict[str, Any]) -> ConnectionVerifyResult:
    bot_token = _text(credentials.get("bot_token") or credentials.get("token"))
    if not bot_token:
        raise HTTPException(status_code=400, detail="Discord bot token is required.")
    try:
        user = discord_connector._discord_api_call("/users/@me", token=bot_token)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Discord bot token could not be verified.") from exc
    account_id = _text(user.get("id")) or None
    global_name = _text(user.get("global_name"))
    username = _text(user.get("username"))
    account_name = global_name or username or (f"Discord bot {account_id}" if account_id else "Discord bot")
    return ConnectionVerifyResult(valid=True, account_name=account_name, account_id=account_id)


def verify_connection(
    *,
    connection_id: str,
    provider: str,
    workspace_id: str,
    tenant_id: Optional[str] = None,
) -> Dict[str, Any]:
    normalized_connection_id = _token(connection_id)
    if normalized_connection_id not in VERIFY_SUPPORTED_CONNECTION_IDS:
        raise HTTPException(status_code=409, detail="Connection verification is not implemented for this connection yet.")
    credentials = _credential_secret(
        workspace_id=workspace_id,
        provider=provider,
        connection_id=normalized_connection_id,
        tenant_id=tenant_id,
    )
    if normalized_connection_id == "telegram_bot":
        result = _verify_telegram_bot(credentials)
    elif normalized_connection_id == "discord_bot":
        result = _verify_discord_bot(credentials)
    else:  # pragma: no cover - guarded above.
        raise HTTPException(status_code=409, detail="Connection verification is not implemented for this connection yet.")
    return {
        "valid": result.valid,
        "account_name": result.account_name,
        "account_id": result.account_id,
    }
