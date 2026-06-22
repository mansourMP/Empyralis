from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server_modules import auth as auth_module
from server_modules import sage_telegram_hosted_service as hosted


router = APIRouter()
get_current_user = auth_module.get_current_user


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

    existing_code = hosted.pairing_code_for_workspace(workspace_id)
    if existing_code:
        is_deep_link = len(existing_code) > hosted.PAIRING_CODE_LENGTH
        return {
            "pairing_code": existing_code,
            "deep_link": hosted.build_deep_link(existing_code) if is_deep_link else None,
            "status": "active",
        }

    # Generate both — user can type the short code or click the deep link
    deep_link_token = hosted.generate_deep_link_token(workspace_id=workspace_id)
    code = hosted.generate_pairing_code(workspace_id=workspace_id)
    return {
        "pairing_code": code,
        "deep_link": hosted.build_deep_link(deep_link_token),
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
    if not hosted.is_configured():
        raise HTTPException(status_code=503, detail="Not configured")

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
    await dispatch_sage_reply_safe(
        transport=_transport,
        workspace_id=workspace_id,
        message=message_text if message_text else "[Media]",
        attachments=_attachments if _attachments else None,
        channel_origin="telegram_hosted",
        sender_id=str(chat_id),
        sender_name=str(parsed.get("from_first_name", "")).strip(),
        reply_to_id=str(parsed.get("message_id") or ""),
    )

    return {"ok": True}


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
