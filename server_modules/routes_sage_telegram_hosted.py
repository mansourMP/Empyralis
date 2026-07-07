from __future__ import annotations

import os
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
import secrets

from server_modules import auth as auth_module
from server_modules import client_identity_service
from server_modules import request_window_quota_adapter
from server_modules import sage_telegram_hosted_service as hosted


router = APIRouter()
get_current_user = auth_module.get_current_user


# The hosted Telegram webhooks are mounted directly under /api, so they bypass
# _dispatch_public_studio_webhook (which throttles every other channel's
# webhook). Reuse the same per-IP request-window limiter here so this is no
# longer an unmetered ingress / forged-flood surface.
_HOSTED_WEBHOOK_RATE_LIMIT_PER_MINUTE = int(
    os.getenv("EMPYRALIS_PUBLIC_WEBHOOK_RATE_LIMIT_PER_MINUTE", "60")
)
_HOSTED_WEBHOOK_RATE_BUCKETS: Dict[str, list] = {}
_HOSTED_WEBHOOK_RATE_LOCK = threading.Lock()


def _enforce_hosted_webhook_rate_limit(request: Request, path: str) -> None:
    client_ip = client_identity_service.resolve_client_ip(request)
    decision = request_window_quota_adapter.evaluate_request_window(
        buckets=_HOSTED_WEBHOOK_RATE_BUCKETS,
        lock=_HOSTED_WEBHOOK_RATE_LOCK,
        key=f"{client_ip}:{path}",
        limit=max(1, _HOSTED_WEBHOOK_RATE_LIMIT_PER_MINUTE),
    )
    if not decision.get("allowed"):
        retry_after = int(decision.get("retry_after_seconds") or 1)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "public_webhook_rate_limited",
                "message": "Telegram webhook ingress is receiving too many requests.",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )


class PairingStartRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)


@router.post("/sage/telegram-hosted/pair/start")
async def start_pairing(
    body: PairingStartRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    workspace_id = auth_module.enforce_workspace_access(
        current_user,
        body.workspace_id,
        minimum_role="admin",
    )
    if not hosted.is_configured():
        raise HTTPException(status_code=503, detail="Sage Telegram hosted bot is not configured")

    # Warm the bot-username cache so build_deep_link can return a real URL
    # even when EMPYRALIS_TELEGRAM_HOSTED_BOT_USERNAME is not set. The token
    # alone is enough — we resolve the username from the Bot API on demand.
    bot_username = await hosted.ensure_bot_username_cached()

    existing_code = hosted.pairing_code_for_workspace(workspace_id)
    if existing_code:
        is_deep_link = len(existing_code) > hosted.PAIRING_CODE_LENGTH
        return {
            "pairing_code": existing_code,
            "deep_link": hosted.build_deep_link(existing_code) if is_deep_link else None,
            "bot_username": bot_username or None,
            "status": "active",
        }

    # Generate both — user can type the short code or click the deep link
    deep_link_token = hosted.generate_deep_link_token(workspace_id=workspace_id)
    code = hosted.generate_pairing_code(workspace_id=workspace_id)
    return {
        "pairing_code": code,
        "deep_link": hosted.build_deep_link(deep_link_token),
        "bot_username": bot_username or None,
        "status": "active",
    }


@router.get("/sage/telegram-hosted/pair/status")
async def pairing_status(
    workspace_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="admin",
    )
    pending_code = hosted.pairing_code_for_workspace(workspace_id)
    already_paired = hosted.is_workspace_paired(workspace_id)
    return {
        "configured": hosted.is_configured(),
        "has_pending_code": pending_code is not None,
        "paired": already_paired,
    }

@router.get("/sage/telegram-hosted/info")
async def bot_info() -> dict:
    if not hosted.is_configured():
        return {"configured": False, "username": None}
    try:
        info = await hosted.get_bot_info()
        bot_data = info.get("result", {}) if isinstance(info.get("result"), dict) else {}
        return {
            "configured": True,
            "username": bot_data.get("username", ""),
            "name": bot_data.get("first_name", ""),
        }
    except Exception:
        return {"configured": True, "username": hosted._bot_username() or None}


@router.post("/sage/telegram-hosted/webhook")
async def telegram_webhook(request: Request) -> dict:
    _enforce_hosted_webhook_rate_limit(request, "/sage/telegram-hosted/webhook")
    if not hosted.is_configured():
        raise HTTPException(status_code=503, detail="Not configured")
    if not hosted.is_webhook_secret_configured():
        # Fail closed: without a configured secret inbound updates cannot be
        # authenticated, so refuse to process them rather than trusting any POST.
        raise HTTPException(status_code=503, detail="Telegram webhook secret is not configured.")

    body_bytes = await request.body()
    header_signature = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")

    if not hosted.verify_webhook_signature(header_signature, body_bytes):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = hosted.parse_telegram_update(body)
    if parsed is None:
        return {"ok": True}

    chat_id = await hosted.handle_inbound_message(parsed)
    if chat_id is None:
        return {"ok": True}

    workspace_id = hosted.get_workspace_for_chat(chat_id)
    if workspace_id is None:
        # Stale pair — the chat_id was paired but the workspace is gone.
        # Tell the user so they don't stare at silence.
        await hosted.send_message_safe(
            chat_id,
            "⚠️ This chat is no longer linked to a workspace. Please re-pair from Empyralis → Connections → Telegram.",
            reply_to_message_id=parsed.get("message_id"),
        )
        return {"ok": True}

    # ── Channel-specific: media attachment resolution ──
    message_text = str(parsed.get("text") or "").strip()
    _attachments: list[dict] = []
    _media = parsed.get("media")
    if isinstance(_media, list) and _media:
        for _m in _media:
            _fid = str(_m.get("file_id") or "").strip()
            if _fid:
                try:
                    _finfo = await hosted.get_file(_fid)
                    _fpath = str(_finfo.get("file_path") or "").strip()
                    _furl = hosted.build_file_url(_fpath) if _fpath else ""
                    _attachments.append({
                        "type": str(_m.get("type") or "file"),
                        "url": _furl,
                        "mime": str(_m.get("mime") or "application/octet-stream"),
                        "filename": str(_m.get("filename") or ""),
                        "file_id": _fid,
                    })
                except Exception:
                    _attachments.append({"type": str(_m.get("type") or "file"), "file_id": _fid})

    # ── Shared command dispatcher ──
    from server_modules.sage_command_dispatcher import dispatch_command
    cmd_reply = await dispatch_command(
        command=message_text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="telegram_hosted",
        sender_id=str(chat_id),
    )
    if cmd_reply is not None:
        await hosted.send_message_safe(
            chat_id, cmd_reply,
            reply_to_message_id=parsed.get("message_id"),
        )
        return {"ok": True}

    # ── Route through shared-core reply dispatcher ──
    # This ONE call owns: typing, execute_sage_turn, error classification,
    # [SILENT] suppression, message splitting, guaranteed fallback.
    # The TelegramHostedTransport provides only the raw send/typing/format primitives.
    from server_modules.sage_reply_dispatcher import dispatch_sage_reply_safe

    _transport = hosted.TelegramHostedTransport(str(chat_id))
    delivered = await dispatch_sage_reply_safe(
        transport=_transport,
        workspace_id=workspace_id,
        message=message_text if message_text else "[Media]",
        attachments=_attachments if _attachments else None,
        channel_origin="telegram_hosted",
        sender_id=str(chat_id),
        sender_name=str(parsed.get("from_first_name", "")).strip(),
        reply_to_id=str(parsed.get("message_id") or ""),
    )

    if not delivered:
        # The reply was generated but could not be delivered after bounded
        # in-band retries. Do NOT ACK success — return 503 so Telegram
        # redelivers the update instead of silently dropping the user's answer.
        raise HTTPException(status_code=503, detail="reply_delivery_failed")

    return {"ok": True}


@router.post("/sage/telegram-hosted/webhook/{pool_bot_id}")
async def telegram_pool_webhook(pool_bot_id: str, request: Request) -> dict:
    """Phase 3B: per-bot webhook. An update delivered here came from exactly one
    pool bot, which is assigned to exactly one agent — so inbound routes to that
    agent with no chat→workspace pairing ambiguity."""
    _enforce_hosted_webhook_rate_limit(request, "/sage/telegram-hosted/webhook/pool")
    from server_modules import hosted_bot_provisioning_service as prov
    from server_modules import hosted_bot_pool_repository as pool_repo

    bot = await pool_repo.get_bot(pool_bot_id)
    if bot is None:
        raise HTTPException(status_code=404, detail="Unknown bot")

    header_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    expected = str(bot.get("webhook_secret") or "")
    if not expected:
        # Fail closed: a pool bot with no webhook secret cannot be authenticated.
        raise HTTPException(status_code=503, detail="Bot webhook secret is not configured.")
    if not secrets.compare_digest(header_secret, expected):
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    parsed = hosted.parse_telegram_update(body)
    if parsed is None:
        return {"ok": True}

    result = await prov.route_hosted_inbound(
        pool_bot_id=pool_bot_id,
        chat_id=parsed["chat_id"],
        message=parsed["text"],
        reply_to_message_id=parsed.get("message_id"),
    )
    if result.get("routed") and not result.get("reply_sent", True):
        # Reply was generated but could not be delivered after bounded
        # in-band retries. Do NOT ACK success — return 503 so Telegram
        # redelivers the update instead of silently dropping the answer.
        raise HTTPException(status_code=503, detail="reply_delivery_failed")
    return {"ok": True, "routing": result}


@router.post("/sage/telegram-hosted/dev-poll")
async def dev_poll_once() -> dict:
    """Dev-only: manually poll Telegram for updates (no webhook needed)."""
    if not hosted.is_configured():
        raise HTTPException(status_code=503, detail="Not configured")
    updates = await hosted.poll_updates(limit=5, timeout=5)
    processed = 0
    for update in updates:
        parsed = hosted.parse_telegram_update(update)
        if parsed is None:
            continue
        chat_id = await hosted.handle_inbound_message(parsed)
        if chat_id is None:
            processed += 1
            continue
        workspace_id = hosted.get_workspace_for_chat(chat_id)
        if workspace_id is None:
            # Stale pair — tell the user
            await hosted.send_message_safe(
                chat_id,
                "⚠️ This chat is no longer linked to a workspace. Please re-pair from Empyralis → Connections → Telegram.",
            )
            processed += 1
            continue
        message_text = str(parsed.get("text") or "").strip()

        # ── Shared command dispatcher (handles /compact, /new, /help, etc.) ──
        from server_modules.sage_command_dispatcher import dispatch_command
        cmd_reply = await dispatch_command(
            command=message_text,
            workspace_id=workspace_id,
            thread_id="sage-main",
            channel_origin="telegram_hosted",
            sender_id=str(chat_id),
        )
        if cmd_reply is not None:
            await hosted.send_message_safe(
                chat_id, cmd_reply,
                reply_to_message_id=parsed.get("message_id"),
            )
            processed += 1
            continue

        # ── Route through shared-core reply dispatcher ──
        from server_modules.sage_reply_dispatcher import dispatch_sage_reply_safe

        _transport = hosted.TelegramHostedTransport(str(chat_id))
        await dispatch_sage_reply_safe(
            transport=_transport,
            workspace_id=workspace_id,
            message=message_text,
            channel_origin="telegram_hosted",
            sender_id=str(chat_id),
            sender_name=str(parsed.get("from_first_name", "")).strip(),
            reply_to_id=str(parsed.get("message_id") or ""),
        )
        processed += 1
    return {"ok": True, "updates_processed": processed}


class UnpairRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=120)


@router.delete("/sage/telegram-hosted/pair")
async def unpair(
    workspace_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    workspace_id = auth_module.enforce_workspace_access(
        current_user,
        workspace_id,
        minimum_role="admin",
    )
    count = hosted.unpair_workspace(workspace_id)
    return {"unpaired": True, "removed": count}


# ── Discord pairing endpoints ──
# Reuse the same pairing-code generator as Telegram.  Codes are
# workspace-scoped and consumable from any channel (first to claim wins).


@router.post("/sage/discord/pair/start")
async def discord_start_pairing(
    body: PairingStartRequest,
    current_user: dict = Depends(get_current_user),
) -> dict:
    # Session check only — no role enforcement. The platform has no roles.
    workspace_id = str(body.workspace_id or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    _tenant_id = auth_module.workspace_tenant_id(current_user, workspace_id)
    _allowed = auth_module.allowed_tenant_ids(current_user)
    if _allowed is not None and _tenant_id not in _allowed:
        raise HTTPException(status_code=403, detail="Workspace is not accessible")
    if not hosted.is_configured():
        raise HTTPException(status_code=503, detail="Discord bot not configured")

    # Reuse the same pending code if one already exists for this workspace
    existing_code = hosted.pairing_code_for_workspace(workspace_id)
    if existing_code:
        return {
            "pairing_code": existing_code,
            "status": "active",
            "oauth_url": _build_discord_oauth_url(workspace_id),
        }

    code = hosted.generate_pairing_code(workspace_id=workspace_id)
    return {
        "pairing_code": code,
        "status": "active",
        "oauth_url": _build_discord_oauth_url(workspace_id),
    }


@router.get("/sage/discord/pair/status")
async def discord_pairing_status(
    workspace_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    # Session check only — no role enforcement. The platform has no roles.
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    _tenant_id = auth_module.workspace_tenant_id(current_user, workspace_id)
    _allowed = auth_module.allowed_tenant_ids(current_user)
    if _allowed is not None and _tenant_id not in _allowed:
        raise HTTPException(status_code=403, detail="Workspace is not accessible")
    pending_code = hosted.pairing_code_for_workspace(workspace_id)
    from server_modules import discord_pairing_service as _dps
    paired = _dps.is_workspace_paired(workspace_id)
    return {
        "has_pending_code": pending_code is not None,
        "paired": paired,
    }


@router.delete("/sage/discord/pair")
async def discord_unpair(
    workspace_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    # Session check only — no role enforcement. The platform has no roles.
    workspace_id = str(workspace_id or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    _tenant_id = auth_module.workspace_tenant_id(current_user, workspace_id)
    _allowed = auth_module.allowed_tenant_ids(current_user)
    if _allowed is not None and _tenant_id not in _allowed:
        raise HTTPException(status_code=403, detail="Workspace is not accessible")
    from server_modules import discord_pairing_service as _dps
    count = _dps.unpair_workspace(workspace_id)
    return {"unpaired": True, "removed": count}


# ── Discord public config (no auth needed) ──

_discord_public_config_cache: dict | None = None


@router.get("/sage/discord/public-config")
async def discord_public_config() -> dict:
    """Return public Discord identifiers — client_id and bot_user_id.

    These are public: the client_id appears in the OAuth install URL and the
    bot_user_id in the "Open bot on Discord" link.  No secrets are returned.
    """
    global _discord_public_config_cache
    if _discord_public_config_cache is not None:
        return _discord_public_config_cache

    import os as _os

    _client_id = ""
    for _key in ("EMPYRALIS_DISCORD_APPLICATION_ID", "DISCORD_CLIENT_ID"):
        _val = str(_os.getenv(_key, "") or "").strip()
        if _val:
            _client_id = _val
            break

    _bot_user_id = str(_os.getenv("DISCORD_BOT_USER_ID", "") or "").strip()
    if not _bot_user_id:
        _token = str(_os.getenv("DISCORD_BOT_TOKEN", "") or "").strip()
        if _token:
            try:
                import httpx
                _resp = httpx.get(
                    "https://discord.com/api/users/@me",
                    headers={"Authorization": f"Bot {_token}"},
                    timeout=10,
                )
                if _resp.status_code == 200:
                    _bot_user_id = str((_resp.json() or {}).get("id") or "").strip()
            except Exception as _exc:
                import logging
                _log = logging.getLogger(__name__)
                _log.warning("Failed to fetch Discord bot user ID: %s", _exc)

    _discord_public_config_cache = {
        "client_id": _client_id,
        "bot_user_id": _bot_user_id,
    }
    return _discord_public_config_cache


# ── Discord OAuth identify-bind ──
# Replaces the pairing-code round-trip with a standard OAuth2 code-grant flow.
# User clicks "Connect with Discord" → authorizes (identify + applications.commands)
# → we exchange the code → fetch their Discord user ID → bind to workspace.
# The pairing-code path is kept as a fallback under a <details> element in the UI.

import time as _time

_OAUTH_STATES: dict = {}  # state_token → {workspace_id, created_at}
_OAUTH_STATE_MAX_AGE_S = 600  # 10 minutes


def _get_discord_client_id() -> str:
    import os as _os
    for _key in ("EMPYRALIS_DISCORD_APPLICATION_ID", "DISCORD_CLIENT_ID"):
        _val = str(_os.getenv(_key, "") or "").strip()
        if _val:
            return _val
    return ""


def _get_discord_client_secret() -> str:
    import os as _os
    return str(_os.getenv("DISCORD_CLIENT_SECRET", "") or "").strip()


def _get_frontend_origin() -> str:
    from server_modules.runtime_config import FRONTEND_ORIGINS
    _origins = [o.strip() for o in str(FRONTEND_ORIGINS or "").split(",") if o.strip()]
    # Prefer an HTTPS origin for OAuth redirects (Discord requires public URLs).
    for _o in _origins:
        if _o.startswith("https://"):
            return _o
    return _origins[0] if _origins else "https://empyralis.ai"


def _build_discord_oauth_url(workspace_id: str) -> str:
    """Build a Discord OAuth2 authorize URL for the identify-bind flow."""
    import urllib.parse as _up
    _client_id = _get_discord_client_id()
    if not _client_id:
        return ""
    _state = secrets.token_urlsafe(32)
    _OAUTH_STATES[_state] = {"workspace_id": str(workspace_id).strip(), "created_at": _time.time()}
    # Clean up expired states
    _now = _time.time()
    for _k in list(_OAUTH_STATES.keys()):
        if _now - _OAUTH_STATES[_k]["created_at"] > _OAUTH_STATE_MAX_AGE_S:
            _OAUTH_STATES.pop(_k, None)
    _redirect_uri = f"{_get_frontend_origin()}/api/sage/discord/oauth/callback"
    return (
        f"https://discord.com/oauth2/authorize"
        f"?client_id={_up.quote(_client_id)}"
        f"&redirect_uri={_up.quote(_redirect_uri)}"
        f"&response_type=code"
        f"&scope={_up.quote('identify applications.commands')}"
        f"&state={_up.quote(_state)}"
    )


@router.get("/sage/discord/oauth/url")
async def discord_oauth_url_endpoint(
    workspace_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the Discord OAuth2 URL for one-click identify-bind.

    Session check only — no role enforcement.  The platform has no roles.
    """
    _wid = str(workspace_id or "").strip()
    if not _wid:
        raise HTTPException(status_code=400, detail="workspace_id is required")
    # Verify the user can access this workspace (tenant membership only, no role gate)
    _tenant_id = auth_module.workspace_tenant_id(current_user, _wid)
    _allowed = auth_module.allowed_tenant_ids(current_user)
    if _allowed is not None and _tenant_id not in _allowed:
        raise HTTPException(status_code=403, detail="Workspace is not accessible")
    _url = _build_discord_oauth_url(_wid)
    if not _url:
        raise HTTPException(status_code=503, detail="Discord client ID is not configured")
    return {"url": _url}


@router.get("/sage/discord/oauth/callback")
async def discord_oauth_callback(code: str, state: str):
    """Handle the Discord OAuth2 redirect after the user authorizes.

    Public endpoint — no auth required (Discord redirects the user's browser here).
    Exchanges the code for an access token, fetches the user's Discord ID,
    binds it to the workspace, and sends a best-effort welcome DM.
    Redirects back to the frontend on completion.
    """
    import logging as _logging
    import json as _json
    import urllib.parse as _up
    _log = _logging.getLogger(__name__)

    # Resolve the state token to get the workspace_id
    _entry = _OAUTH_STATES.pop(str(state).strip(), None)
    if _entry is None:
        _frontend = _get_frontend_origin()
        return RedirectResponse(url=f"{_frontend}?discord=error&reason=invalid_state")

    _wid = str(_entry.get("workspace_id") or "").strip()
    _frontend = _get_frontend_origin()

    if not _wid:
        return RedirectResponse(url=f"{_frontend}?discord=error&reason=missing_workspace")

    _client_id = _get_discord_client_id()
    _client_secret = _get_discord_client_secret()

    if not _client_id or not _client_secret:
        _log.error("Discord OAuth: client_id or client_secret missing")
        return RedirectResponse(url=f"{_frontend}?discord=error&reason=not_configured")

    _redirect_uri = f"{_frontend}/api/sage/discord/oauth/callback"

    # ── Exchange code for access token ──
    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as _client:
            _token_resp = await _client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": _client_id,
                    "client_secret": _client_secret,
                    "grant_type": "authorization_code",
                    "code": str(code).strip(),
                    "redirect_uri": _redirect_uri,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if _token_resp.status_code != 200:
                _log.warning("Discord OAuth: token exchange failed status=%s body=%s",
                             _token_resp.status_code, (_token_resp.text or "")[:200])
                return RedirectResponse(url=f"{_frontend}?discord=error&reason=token_exchange_failed")
            _token_data = _token_resp.json()
            _access_token = str(_token_data.get("access_token") or "").strip()
            if not _access_token:
                return RedirectResponse(url=f"{_frontend}?discord=error&reason=no_access_token")
    except Exception as _exc:
        _log.exception("Discord OAuth: token exchange error")
        return RedirectResponse(url=f"{_frontend}?discord=error&reason=token_exchange_error")

    # ── Fetch Discord user ID ──
    try:
        import httpx
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as _client:
            _user_resp = await _client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {_access_token}"},
            )
            if _user_resp.status_code != 200:
                _log.warning("Discord OAuth: user fetch failed status=%s", _user_resp.status_code)
                return RedirectResponse(url=f"{_frontend}?discord=error&reason=user_fetch_failed")
            _user_data = _user_resp.json()
            _discord_user_id = str(_user_data.get("id") or "").strip()
            if not _discord_user_id:
                return RedirectResponse(url=f"{_frontend}?discord=error&reason=no_user_id")
    except Exception as _exc:
        _log.exception("Discord OAuth: user fetch error")
        return RedirectResponse(url=f"{_frontend}?discord=error&reason=user_fetch_error")

    # ── Bind Discord user → workspace ──
    from server_modules import discord_pairing_service as _dps
    _dps.pair_discord_workspace(_discord_user_id, _wid)
    _log.info("Discord OAuth: paired user %s → workspace %s", _discord_user_id, _wid)

    # ── Best-effort welcome DM ──
    _dm_ok = False
    try:
        import os as _os
        import httpx
        _bot_token = str(_os.getenv("DISCORD_BOT_TOKEN", "") or "").strip()
        if _bot_token:
            async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as _client:
                _dm_resp = await _client.post(
                    "https://discord.com/api/v10/users/@me/channels",
                    json={"recipient_id": _discord_user_id},
                    headers={"Authorization": f"Bot {_bot_token}"},
                )
                if _dm_resp.status_code == 200:
                    _dm_data = _dm_resp.json()
                    _channel_id = str(_dm_data.get("id") or "").strip()
                    if _channel_id:
                        await _client.post(
                            f"https://discord.com/api/v10/channels/{_channel_id}/messages",
                            json={"content": "✅ You're now connected to Empyralis! Your workspace is linked. Send me any message to start."},
                            headers={"Authorization": f"Bot {_bot_token}"},
                        )
                        _dm_ok = True
    except Exception as _exc:
        _log.warning("Discord OAuth: welcome DM failed for user %s (best-effort, proceeding): %s",
                     _discord_user_id, _exc)

    # ── Redirect to frontend ──
    _params = "section=channels&discord=connected" if _dm_ok else "section=channels&discord=connected&dm=pending"
    return RedirectResponse(url=f"{_frontend}/w/{_up.quote(_wid)}/integrations?{_params}")
