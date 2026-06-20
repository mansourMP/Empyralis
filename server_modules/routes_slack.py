"""Slack inbound message handler — routes through the unified Sage ingress.

Messages arrive via Slack Events API. Commands are handled by the shared
sage_command_dispatcher; normal messages flow through execute_sage_turn().
"""

from __future__ import annotations

import os
import hmac
import hashlib
import time
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request

from server_modules.runtime_common import require_api_key
from server_modules.channel_adapter import filter_outbound_reply

router = APIRouter()

# ── Slack config (read from env, graceful if missing) ──

_SLACK_SIGNING_SECRET = str(os.environ.get("SLACK_SIGNING_SECRET", "")).strip()
_SLACK_BOT_TOKEN = str(os.environ.get("SLACK_BOT_TOKEN", "")).strip()
_SLACK_CLIENT_ID = str(os.environ.get("SLACK_CLIENT_ID", "")).strip()
_SLACK_CLIENT_SECRET = str(os.environ.get("SLACK_CLIENT_SECRET", "")).strip()

import logging
_logger = logging.getLogger(__name__)

if not _SLACK_SIGNING_SECRET or not _SLACK_BOT_TOKEN:
    _logger.warning(
        "Slack not fully configured — set SLACK_SIGNING_SECRET and SLACK_BOT_TOKEN "
        "env vars to enable Slack integration"
    )


def _verify_slack_signature(body_bytes: bytes, headers: dict) -> bool:
    """Verify Slack request signature."""
    if not _SLACK_SIGNING_SECRET:
        return False
    timestamp = str(headers.get("x-slack-request-timestamp", "")).strip()
    signature = str(headers.get("x-slack-signature", "")).strip()
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 300:
            return False
    except Exception:
        return False
    sig_basestring = f"v0:{timestamp}:{body_bytes.decode('utf-8')}"
    computed = "v0=" + hmac.new(
        _SLACK_SIGNING_SECRET.encode("utf-8"),
        sig_basestring.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


@router.post("/sage/slack/inbound")
async def slack_inbound(
    request: Request,
    current_user=Depends(require_api_key),
) -> dict:
    """Receive inbound Slack messages and route through Sage."""
    body_bytes = await request.body()

    if _SLACK_SIGNING_SECRET and not _verify_slack_signature(body_bytes, dict(request.headers)):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event = body.get("event") if isinstance(body.get("event"), dict) else {}
    event_type = str(event.get("type") or "").strip()

    # Slack URL verification challenge
    if str(body.get("type") or "").strip() == "url_verification":
        return {"challenge": str(body.get("challenge") or "")}

    if event_type not in {"message", "app_mention"}:
        return {"ok": True}

    text = str(event.get("text") or "").strip()
    channel_id = str(event.get("channel") or "").strip()
    user_id = str(event.get("user") or "").strip()
    workspace_id = str(body.get("workspace_id") or "default").strip()

    if not text or not channel_id:
        return {"ok": True}

    # ── Shared command dispatcher ──
    from server_modules.sage_command_dispatcher import dispatch_command
    cmd_reply = await dispatch_command(
        command=text,
        workspace_id=workspace_id,
        thread_id="sage-main",
        channel_origin="slack",
        sender_id=user_id or None,
    )
    if cmd_reply is not None:
        await _slack_reply(channel_id, cmd_reply)
        return {"ok": True, "command_handled": True, "reply": cmd_reply}

    # ── Normal message: route through unified Sage ingress ──
    from server_modules.sage_turn_adapter import execute_sage_turn

    result = await execute_sage_turn(
        workspace_id=workspace_id,
        message=text,
        channel_origin="slack",
        channel_sender_id=user_id,
        channel_sender_name=str(event.get("user_name") or "").strip(),
    )

    reply = filter_outbound_reply(result.message)
    if reply:
        await _slack_reply(channel_id, reply)

    return {"ok": True, "sage_replied": bool(reply)}


async def _slack_reply(channel_id: str, text: str) -> None:
    """Send a reply to a Slack channel. No-op if Slack not configured."""
    if not _SLACK_BOT_TOKEN:
        _logger.warning("Cannot send Slack reply — SLACK_BOT_TOKEN not set")
        return
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {_SLACK_BOT_TOKEN}"},
                json={"channel": channel_id, "text": text},
            )
    except Exception as exc:
        _logger.warning("Slack reply failed: %s", exc)


@router.post("/api/channels/slack/oauth/callback")
async def slack_oauth_callback(
    request: Request,
) -> dict:
    """Handle Slack OAuth redirect — exchanges code for token and stores team mapping."""
    body = await request.json() if await request.body() else {}
    code = str(body.get("code") or "").strip()
    workspace_id = str(body.get("workspace_id") or body.get("state") or "").strip()

    if not code:
        raise HTTPException(status_code=400, detail="Missing OAuth code")
    if not _SLACK_CLIENT_ID or not _SLACK_CLIENT_SECRET:
        raise HTTPException(status_code=503, detail="Slack OAuth not configured")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://slack.com/api/oauth.v2.access",
                data={
                    "client_id": _SLACK_CLIENT_ID,
                    "client_secret": _SLACK_CLIENT_SECRET,
                    "code": code,
                },
            )
            data = resp.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Slack OAuth exchange failed: {exc}")

    if not data.get("ok"):
        raise HTTPException(status_code=400, detail=f"Slack OAuth error: {data.get('error', 'unknown')}")

    team_id = str((data.get("team") or {}).get("id") or "").strip()
    bot_token = str(data.get("access_token") or "").strip()

    if workspace_id and team_id:
        try:
            from server_modules.control_plane_repository import _pool
            async with _pool.acquire() as conn:
                await conn.execute(
                    "UPDATE workspaces SET slack_team_id = $1 WHERE id = $2",
                    team_id, workspace_id,
                )
        except Exception as exc:
            _logger.warning("Failed to store Slack team mapping: %s", exc)

    return {
        "ok": True,
        "team_id": team_id,
        "team_name": str((data.get("team") or {}).get("name") or ""),
        "workspace_id": workspace_id,
    }
